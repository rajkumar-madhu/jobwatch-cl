# On-call schedules and escalation

Decide who gets paged when a job fails, and keep paging until someone acknowledges. Team plan and up;
**On-call** in the app.

## Schedules
A schedule has one or more **layers**. Each layer rotates an ordered list of people from a first
handoff time, in the schedule's time zone:

| Rotation | Hands over |
|---|---|
| Every day | daily at the handoff's local time |
| Every week | every 7 days at the handoff's local time |
| Every N hours | every N hours of elapsed time (1 to 720) |

Daily and weekly handoffs keep their local time across daylight-saving changes. A layer can be
restricted to some weekdays and a time of day (for example weekdays 09:00 to 17:00, or Friday 18:00 to
Saturday 09:00). Outside it, that layer has nobody. **The highest layer with someone on call wins**,
so a business-hours layer can sit above a 24/7 one.

**Overrides** ("Cy covers Tuesday evening") beat every layer. The schedule page shows who is on call
now and the upcoming shifts.

## Escalation policies
A policy is a list of steps. Each step pages **whoever is on call in some schedules** and/or **named
people**, then waits for an acknowledgement before paging the next step. After the last step the
policy can **repeat** up to 9 times.

Pages are delivered through one of your alert channels:
- **Email** goes to the paged people's own addresses.
- **Slack, Teams, Discord, Telegram and webhooks** post the alert, starting with who is being paged.

Every page includes the alert's full context and an acknowledge link.

> [!NOTE]
> JobWatch pages by email, chat and webhooks. It does not make phone calls or send SMS; to page by
> phone, send alerts to PagerDuty or Opsgenie instead.

## Route alerts to a policy
On the policy, choose **Route alerts here**. This creates an alert channel ("On call: Platform") that
alert rules can send to like any other channel.

## When paging stops
- Someone **acknowledges**: on the On-call page, through the link in the page, or by acknowledging the
  incident. Acknowledging the page also acknowledges its incident.
- The incident is **resolved**, or the **job recovers**.
- The policy has run all its steps and repeats ("exhausted").

The same alert never starts a second escalation while one is running. If a schedule has nobody on
call at that moment, the page history says so and the policy moves on to its next step. Every change
to schedules and policies, and every acknowledgement, is in the audit log.

## API
`/api/v1/oncall/schedules` (layers, overrides, `?days=` timeline), `/api/v1/oncall/policies`,
`/api/v1/oncall/escalations` and `POST /api/v1/oncall/escalations/{id}/ack`.
