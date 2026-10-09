# Directory sync (SCIM): people and groups (R44, R56)

JobWatch speaks SCIM 2.0 to Okta, Microsoft Entra ID and other directories, on the Business and
Enterprise plans.

## Set up
1. **Settings → Single sign-on:** verify your email domain(s) and, as an owner, create a SCIM token.
2. In your directory's JobWatch app, set the SCIM base URL shown on that page and the token
   (Bearer). Enable "push users" and, for roles, "push groups".

## People
- Created when assigned, updated when their name changes, and **deactivated or deleted the moment
  they are unassigned**; their next request to JobWatch is refused.
- Only addresses in your verified domains can be provisioned.
- **Owners are never changed** by directory sync, so a sync mistake cannot remove the people who can
  fix it.

## Groups and roles
- Pushed groups appear under **Settings → Single sign-on → Groups and roles**.
- An **owner** gives a group a JobWatch role (viewer, developer, sre, devops or admin; never owner).
  Everyone in the group gets that role; someone in several mapped groups gets the **highest**.
- Unmapped groups do not change anyone's role.
- When someone leaves every mapped group, a role that a group gave them reverts to the default role
  (set with SSO). A role an admin set by hand, for someone in no mapped group, is left alone.
- Deactivated people stay without access whatever their groups; when reactivated they come back with
  the role their groups give them.
- Every role change is in the audit log (`scim.role_sync`) with who caused it: the directory (SCIM
  token) or the owner who changed a mapping.

Both Okta's and Entra ID's group update formats are supported.
