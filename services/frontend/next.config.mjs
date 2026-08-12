/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emits .next/standalone with only the files the server actually needs, so the
  // runtime image carries a traced subset instead of the whole node_modules tree.
  output: "standalone",
};
export default nextConfig;
