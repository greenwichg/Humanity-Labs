# Offline checks of the deploy role's guard rails (mocked AWS provider; no credentials needed).
mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_iam_policy_document" { defaults = { json = "{}" } }
  mock_data "aws_route53_zone" { defaults = { zone_id = "Z0HUMANITYLABS" } }
}

run "deploy_role_guard_rails" {
  command = plan

  # Every IAM permission except service-linked roles applies only to <project>-ecs-* roles, so the deploy role
  # cannot edit itself, the backup role, or any other role in the account.
  assert {
    condition = alltrue([
      for st in data.aws_iam_policy_document.deploy.statement :
      st.resources == toset(["arn:aws:iam::111122223333:role/humanity-labs-ecs-*"])
      if coalesce(st.effect, "Allow") == "Allow" && anytrue([for a in st.actions : startswith(a, "iam:") && a != "iam:CreateServiceLinkedRole"])
    ])
    error_message = "IAM permissions of the deploy role must be limited to humanity-labs-ecs-* roles."
  }

  assert {
    condition = alltrue([
      for st in data.aws_iam_policy_document.deploy.statement :
      anytrue([for c in st.condition : c.variable == "iam:PermissionsBoundary" && contains(c.values, "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary")])
      if contains(st.actions, "iam:CreateRole")
    ])
    error_message = "Roles created by the deploy role must carry the ECS permissions boundary."
  }

  assert {
    condition = anytrue([
      for st in data.aws_iam_policy_document.deploy.statement :
      st.effect == "Deny" && length(setsubtract(["rds:DeleteDBSnapshot", "rds:ModifyDBSnapshotAttribute", "rds:DeleteDBInstanceAutomatedBackup"], st.actions)) == 0
    ])
    error_message = "The deploy role must be denied deleting or sharing snapshots and retained backups."
  }

  # DNS changes: only in the site's hosted zone, and only for the domain, www and certificate validation names.
  assert {
    condition = alltrue([
      for st in data.aws_iam_policy_document.deploy.statement :
      st.resources == toset(["arn:aws:route53:::hostedzone/Z0HUMANITYLABS"]) && anytrue([
        for c in st.condition : c.test == "ForAllValues:StringLike" && c.variable == "route53:ChangeResourceRecordSetsNormalizedRecordNames" && toset(c.values) == toset(["humanitylabs.in", "www.humanitylabs.in", "_*.humanitylabs.in"])
      ])
      if contains(st.actions, "route53:ChangeResourceRecordSets")
    ]) && anytrue([for st in data.aws_iam_policy_document.deploy.statement : contains(st.actions, "route53:ChangeResourceRecordSets")])
    error_message = "The deploy role may change only the site's own records in its hosted zone."
  }

  assert {
    condition     = !anytrue([for st in data.aws_iam_policy_document.deploy.statement : contains(st.actions, "iam:UpdateAssumeRolePolicy")])
    error_message = "The deploy role must not be able to change who can assume roles."
  }
}
