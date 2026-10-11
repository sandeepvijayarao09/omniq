---
org_role: director
title: Director
rank: 2
---

# Role: Director

## What this role is responsible for
- Runs a function (engineering or product) through managers and leads.
- Owns delivery against the roadmap, team health, hiring and cross-team trade-offs.
- Escalates to the CTO; unblocks the people who report to them.

## How to frame answers for this role
Frame answers as status, risks and the trade-off to decide. Name owners and dates.

## What my role may access (IAM)
Role chain: director → developer → employee → product_manager. Allow statements: director.ReadHRForManagedTeamsOnly, director.UseDirectorAgents, director.ImproveAndApproveTeamPlaybooks, director.SeeAllProjects, developer.ReadEngineeringAndCustomerContext, developer.UseDeveloperAgents, employee.ReadCompanyWide, employee.ReadOwnPersonalFiles, employee.UseChat, employee.ManageOwnMemory, employee.ReadOwnProjects, product_manager.ReadEngineeringAndCustomerContext, product_manager.UseProductAgents.
Anything not allowed is denied; answers must respect this.
