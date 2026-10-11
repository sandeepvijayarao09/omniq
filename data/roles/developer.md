---
org_role: developer
title: Developer
rank: 1
---

# Role: Developer

## What this role is responsible for
- Designs, builds, reviews and operates services on the Pulse platform.
- Holds the pager on rotation and follows the deploy and incident runbooks.
- Cares about correctness, the exact commands and numbers, and the relevant ADR.

## How to frame answers for this role
Be concrete: exact numbers, commands, file or system names, and the ADR or runbook section.

## What my role may access (IAM)
Role chain: developer → employee. Allow statements: developer.ReadEngineeringAndCustomerContext, developer.UseDeveloperAgents, employee.ReadCompanyWide, employee.ReadOwnPersonalFiles, employee.UseChat, employee.ManageOwnMemory, employee.ReadOwnProjects.
Anything not allowed is denied; answers must respect this.
