# The domain and www point at the load balancer (alias records: no extra cost, follow the load balancer's
# addresses). They live in this stack because the load balancer does: removing the app layer removes them too, and
# "AWS provision" puts them back. The hosted zone itself is never touched.

resource "aws_route53_record" "apex" {
  count   = local.https ? 1 : 0
  zone_id = local.zone_id
  name    = local.domain
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "www" {
  count   = local.https ? 1 : 0
  zone_id = local.zone_id
  name    = "www.${local.domain}"
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }
}
