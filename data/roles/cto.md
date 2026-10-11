---
org_role: cto
title: CTO
rank: 3
---

# Role: CTO

## What this role is responsible for
- Owns technical strategy, architecture direction and engineering spend.
- Answers to the CEO and the board for reliability, security and delivery.
- Decides build vs buy, platform bets and org design for engineering.

## How to frame answers for this role
Lead with the business impact and the decision needed. Three bullets max, then risks. No implementation detail unless asked.

## What my role may access (IAM)
Role chain: cto → director → developer → employee → product_manager. Allow statements: cto.ReadBoardFinanceAndAllHR, cto.UseEveryAgent, cto.GovernEveryPlaybook, director.ReadHRForManagedTeamsOnly, director.UseDirectorAgents, director.ImproveAndApproveTeamPlaybooks, director.SeeAllProjects, developer.ReadEngineeringAndCustomerContext, developer.UseDeveloperAgents, employee.ReadCompanyWide, employee.ReadOwnPersonalFiles, employee.UseChat, employee.ManageOwnMemory, employee.ReadOwnProjects, product_manager.ReadEngineeringAndCustomerContext, product_manager.UseProductAgents.
Anything not allowed is denied; answers must respect this.
