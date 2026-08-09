"use client";

import { useCallback, useRef, useState } from "react";

import { blobToBase64 } from "./utils";

/**
 * Pick a MIME type the browser can actually record.
 *
 * Chrome and Firefox produce WebM/Opus; Safari produces MP4/AAC. Deepgram sniffs
 * the container, so no format hint is sent with the upload — but the browser
 * still has to be asked for something it supports, and passing an unsupported
 * type to MediaRecorder throws. An empty string means "browser default", which
 * is always safe.
 */
function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

export interface RecorderState {
  recording: boolean;
  /** True when the browser has no microphone API at all (or an insecure origin). */
  unsupported: boolean;
  start: () => Promise<void>;
  /** Stops recording and resolves the audio as base64, or null if nothing was captured. */
  stop: () => Promise<string | null>;
  cancel: () => void;
}

export function useRecorder(): RecorderState {
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const unsupported =
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof MediaRecorder === "undefined";

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (unsupported) throw new Error("This browser can't record audio.");

    // Throws if the user denies permission; the caller turns that into a message
    // telling them to type instead.
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamRef.current = stream;
    chunksRef.current = [];

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  }, [unsupported]);

  const stop = useCallback(async (): Promise<string | null> => {
    const recorder = recorderRef.current;
    if (!recorder) return null;

    const finished = new Promise<Blob | null>((resolve) => {
      recorder.onstop = () => {
        const chunks = chunksRef.current;
        resolve(chunks.length ? new Blob(chunks, { type: recorder.mimeType }) : null);
      };
    });

    recorder.stop();
    setRecording(false);
    const blob = await finished;
    releaseStream();
    recorderRef.current = null;

    if (!blob || blob.size === 0) return null;
    return blobToBase64(blob);
  }, [releaseStream]);

  const cancel = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
    }
    recorderRef.current = null;
    chunksRef.current = [];
    releaseStream();
    setRecording(false);
  }, [releaseStream]);

  return { recording, unsupported, start, stop, cancel };
}
