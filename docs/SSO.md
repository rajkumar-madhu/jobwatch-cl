# Single sign-on

Business and Enterprise organizations can sign in through their own identity provider: Okta,
Microsoft Entra ID, Google Workspace, or any SAML 2.0 or OpenID Connect provider.

## Set up (Settings → Single sign-on, admins)
1. **Connect your identity provider.** For OpenID Connect enter the issuer URL, client ID and client
   secret; for SAML the IdP entity ID, sign-on URL and signing certificate. Register the redirect (ACS)
   URL shown on the page with your provider.
2. **Verify your email domains.** Add the domain, publish the TXT record shown, then **Check DNS**.
   Only addresses in verified domains can sign in through your provider; public mailbox domains
   (gmail.com and the like) cannot be claimed, and a domain belongs to one organization only.
3. **Choose the role for new members.** People who sign in for the first time join with this role.

## Signing in
On the sign-in page, people enter their work email and go straight to your provider.

## Require single sign-on (owners)

> [!IMPORTANT]
> Owners always keep password sign-in, even when single sign-on is required. Keep at least two owners
> so a broken identity provider can never lock your organization out.

Once on, everyone except owners can use the organization only from an SSO session; existing password
sessions stop working. **Owners keep password sign-in**, so a broken identity provider cannot lock the
organization out. API keys are not affected. Disabling the provider turns the requirement off.

## Next
Let your directory add and remove people, and map groups to roles, with [directory sync (SCIM)](scim).
