# ---------------------------------------------------------------- community photos (designed; off until approved)
#
# A private bucket for photos shared in Community. It is created only when `community_media = true`, which needs the
# owner's approval: until then nothing here exists, and photo uploads stay off in production (the app refuses them
# unless BATCHLIGHT_COMMUNITY_UPLOADS is set). Photos are served through the app, never straight from S3, so the
# bucket stays fully private.

locals {
  media_bucket     = "${var.project}-media-${data.aws_caller_identity.current.account_id}"
  media_bucket_arn = "arn:${data.aws_partition.current.partition}:s3:::${local.media_bucket}"
}

resource "aws_s3_bucket" "media" {
  count  = var.community_media ? 1 : 0
  bucket = local.media_bucket
  lifecycle { prevent_destroy = true } # contributors' photos are evidence other people rely on
}

resource "aws_s3_bucket_versioning" "media" {
  count  = var.community_media ? 1 : 0
  bucket = aws_s3_bucket.media[0].id
  versioning_configuration { status = "Enabled" } # recovers a photo deleted or overwritten by mistake
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  count  = var.community_media ? 1 : 0
  bucket = aws_s3_bucket.media[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "media" {
  count                   = var.community_media ? 1 : 0
  bucket                  = aws_s3_bucket.media[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  count  = var.community_media ? 1 : 0
  bucket = aws_s3_bucket.media[0].id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  count  = var.community_media ? 1 : 0
  bucket = aws_s3_bucket.media[0].id
  rule {
    id     = "expire-replaced-versions"
    status = "Enabled"
    filter {}
    # The original upload is replaced once faces and number plates are blurred: keep the unblurred version no longer
    # than needed to recover from a mistake.
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_s3_bucket_policy" "media" {
  count  = var.community_media ? 1 : 0
  bucket = aws_s3_bucket.media[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "HttpsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [local.media_bucket_arn, "${local.media_bucket_arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.media]
}
