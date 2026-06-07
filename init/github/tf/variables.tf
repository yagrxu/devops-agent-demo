variable "project_name" {
  type        = string
  description = "Project name used for resource naming"
  default     = "devops-agent-demo"
}

variable "github_org" {
  type        = string
  description = "GitHub organization or username"
}

variable "github_repo" {
  type        = string
  description = "GitHub repository name (without org prefix)"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI profile name"
}

variable "aws_region" {
  type        = string
  description = "AWS region for deployment"
}

variable "branch" {
  type        = string
  description = "Branch that triggers deployment"
  default     = "main"
}

variable "tags" {
  type        = map(string)
  description = "Common tags for all resources"
  default = {
    Project   = "devops-agent-demo"
    ManagedBy = "terraform"
    Component = "oidc-bootstrap"
  }
}
