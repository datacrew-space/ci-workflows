# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the label
strings used in this repo's issue tracker.

| Role              | Label in this repo | Meaning                                  |
| ----------------- | ------------------ | ---------------------------------------- |
| `needs-triage`    | `needs-triage`     | Maintainer needs to evaluate this issue  |
| `needs-info`      | `needs-info`       | Waiting on reporter for more information |
| `ready-for-agent` | `ready-for-agent`  | Fully specified, ready for an AFK agent  |
| `ready-for-human` | `ready-for-human`  | Requires human implementation            |
| `wontfix`         | `wontfix`          | Will not be actioned                     |

These labels are documented here only. They may not exist in the repository yet; a maintainer
creates them in the GitHub UI or with `gh label create`. Until then, skills that apply a label
will fail or skip it, so check with `gh label list` first.
