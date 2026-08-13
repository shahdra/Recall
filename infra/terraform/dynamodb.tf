# The three DynamoDB tables Recall stores everything in.
#
# LOCAL MIRROR: scripts/setup-local-dynamodb.sh creates the same three tables in
# DynamoDB Local, and says of itself: "Key schemas here must match
# infra/terraform/dynamodb.tf. If they drift, local runs pass while production
# breaks." Any change to a key schema or index below must be made there too.
#
# Billing is PAY_PER_REQUEST throughout. Provisioned capacity would mean guessing
# a read/write rate for a study app used by a handful of learners, and paying for
# the guess around the clock; on-demand costs nothing when idle, which is most of
# the time.

locals {
  # Applied to all three names. Empty prefix ("") yields the bare names the
  # services already default to, which is what local development uses.
  cards_table   = "${var.table_name_prefix}${var.cards_table_name}"
  decks_table   = "${var.table_name_prefix}${var.decks_table_name}"
  profile_table = "${var.table_name_prefix}${var.profile_table_name}"
}

# Flashcards plus their SM-2 scheduling state (ease_factor, interval_days,
# repetitions, due_date). One item per card.
#
# Partitioned by deck_id so reading a whole deck is one Query. The card_id range
# key makes (deck, card) the unique identity, which is also the shape every write
# uses: study-mcp updates a card by deck_id + card_id after grading.
resource "aws_dynamodb_table" "cards" {
  name         = local.cards_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "deck_id"
  range_key    = "card_id"

  # Only key and index attributes are declared. DynamoDB is schemaless for
  # everything else — front, back, topic, ease_factor and the rest are written
  # without being declared here, and declaring an unused attribute is an error.
  attribute {
    name = "deck_id"
    type = "S"
  }

  attribute {
    name = "card_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  # ISO-8601 date STRING, not a number. storage.query_due_cards filters with
  # Key("due_date").lte(today) and relies on "2026-08-09" < "2026-08-10"
  # lexicographically. A numeric epoch would work too, but every other layer —
  # the API responses, the demo clock, the reminder digest — speaks ISO dates.
  attribute {
    name = "due_date"
    type = "S"
  }

  # The index behind "what should I study today?" — the one query the whole app is
  # built around (services/study-mcp/storage.py:133).
  #
  # Partitioned by user_id, NOT due_date: a learner's due cards must come back in
  # a single Query. Partitioning by due_date would require querying one partition
  # per day in the range and would hot-spot every writer on today's date.
  #
  # docs/spec.md:143 describes this as a GSI "on due_date", which reads as
  # due_date being the partition key. The code is the authority and it uses
  # user_id + due_date; docs/plan.md:823 agrees.
  global_secondary_index {
    name = "due-index"

    # Spelled as a key_schema block rather than the hash_key/range_key pair used
    # on the table itself. Inside global_secondary_index those two arguments are
    # deprecated as of AWS provider v6; on the table they are still current, which
    # is why the two levels here look inconsistent.
    key_schema {
      attribute_name = "user_id"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "due_date"
      key_type       = "RANGE"
    }

    # ALL, deliberately. The consumers read non-key attributes straight off the
    # returned items — _normalize_card (storage.py:89) reads ease_factor,
    # interval_days and repetitions, and the tutor-agent reads front/back/topic —
    # so KEYS_ONLY or INCLUDE would force a follow-up GetItem per card and turn
    # one Query into N+1 round trips.
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  # Encryption at rest is on by default with an AWS-owned key; being explicit
  # documents that it was considered rather than defaulted into.
  server_side_encryption {
    enabled = true
  }

  # A table rename destroys and recreates it, silently losing every card and all
  # review history. Names come from variables, so a typo in tfvars is exactly how
  # that would happen.
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = local.cards_table
  }
}

# One item per deck (uploaded material). Partitioned by user_id with deck_id as
# range key, so "list my decks" is one Query and no learner can see another's.
resource "aws_dynamodb_table" "decks" {
  name         = local.decks_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "deck_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "deck_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = local.decks_table
  }
}

# The long-term learner profile: weak topics, strengths, totals reviewed. One item
# per learner, always fetched by id — no range key and no index, because there is
# no access pattern other than "get this learner's profile".
#
# This is the table that makes the agent's memory observable across sessions, and
# the one the reminder CronJob scans to discover which learners exist.
resource "aws_dynamodb_table" "learner_profile" {
  name         = local.profile_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = local.profile_table
  }
}
