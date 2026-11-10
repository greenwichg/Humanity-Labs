# The production domain, registered in Amazon Route 53. Registering a domain there creates its public hosted zone,
# so Terraform only looks the zone up: it never creates, changes or deletes the zone or the registration.
#
# Here: one ACM certificate for the domain and www, validated by DNS records in that zone. ACM renews it
# automatically for as long as those records stay. The app stack adds the records that point the domain and www at
# the load balancer, and the www and HTTP redirects.

locals {
  has_domain  = var.domain_name != ""
  cert_names  = local.has_domain ? toset([var.domain_name, "www.${var.domain_name}"]) : toset([])
  certificate = local.has_domain ? aws_acm_certificate_validation.site[0].certificate_arn : ""
}

data "aws_route53_zone" "site" {
  count        = local.has_domain ? 1 : 0
  name         = var.domain_name
  private_zone = false
}

resource "aws_acm_certificate" "site" {
  count                     = local.has_domain ? 1 : 0
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"
  tags                      = { Name = var.domain_name }

  lifecycle {
    create_before_destroy = true
  }
}

# One validation record per name. Keyed by the names from the configuration so the plan is known in advance.
resource "aws_route53_record" "certificate_validation" {
  for_each = local.cert_names
  zone_id  = data.aws_route53_zone.site[0].zone_id
  name     = one([for o in aws_acm_certificate.site[0].domain_validation_options : o.resource_record_name if o.domain_name == each.key])
  type     = one([for o in aws_acm_certificate.site[0].domain_validation_options : o.resource_record_type if o.domain_name == each.key])
  records  = [one([for o in aws_acm_certificate.site[0].domain_validation_options : o.resource_record_value if o.domain_name == each.key])]
  ttl      = 300
}

# Waits until ACM has issued the certificate (usually a few minutes once the records exist).
resource "aws_acm_certificate_validation" "site" {
  count                   = local.has_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [for r in aws_route53_record.certificate_validation : r.fqdn]
}
