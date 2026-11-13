# Glossary

The words this organization uses. Definitions only; decisions live in `docs/adr/` and plans in
`docs/org/`. When a term here conflicts with how you were about to use a word, use the term here.

## Organization

**datacrew-space org**
The public GitHub organization that owns DataCrew's public products: the mdrag product family and
the crew-logger library. It holds public content only. Every repository in it is public from its
first push.
_Avoid_: "DataCrew" alone (also a company and a community); "the org" for any personal account.

**Org home**
The public org repository an agent starts in. It holds the shared rules, shared skills, cross-repo
ADRs, this glossary, reusable CI and pointers to the org's other repositories. Which repository
plays this role is still open (see `docs/org/README.md`).
_Avoid_: "umbrella"; "factory"; "monorepo".

**Personal home**
A maintainer's own private home repository, outside this org. It points at the org home and holds
anything private, including the private-terms denylist. Nothing in this org names or links to it.
_Avoid_: putting anything an org member needs only there.

**Home Repo pattern**
Running every agent session from one repository that holds the shared skills, agent instructions
and docs, plus pointers to every other repository. Source: Matt Pocock,
<https://www.aihero.dev/the-home-repo-pattern>.

**Template repository**
The skeleton every new org repository is created from: empty stubs, both gates and the pointer back
to the org home, with no content of its own.

## Publishing safely

**Public-repo guards**
The two permanent, blocking gates every org repository runs in pre-commit and CI: the secret scan
and the private-terms scan. See `docs/guards.md`.

**Secret scan**
The gate that refuses tokens, keys and private keys, using an established scanner.

**Private-terms scan**
The gate that refuses text matching the private-terms denylist. Its reports name the commit, file,
line and entry number, never the matched text.

**Private-terms denylist**
The list of names that would reveal a private layout: private repository, host and account names,
personal email domains, local paths. It never lives in a public repository; CI receives it as a
secret.
_Avoid_: "blocklist", "wordlist".

**Privacy checklist**
The written review a repository passes before it is created in the org. Creating an org repository
is publishing it, so the review comes first.

**Fresh history**
A repository that moves into the org starts at v1 from a cleaned tree, with none of its old commits.
The old repository is archived privately.
_Avoid_: "squash", "rewrite history" (both imply the old commits are carried over).

**Rotation**
Replacing a credential that ever appeared in a commit, so every copy already read is useless.
Deleting the commit is not enough.

## mdrag product family

**mdrag product family**
mdrag's core plus its satellites (fetch providers, the feed factory, transcripts, auth and the other
first-party pieces).
_Avoid_: "mdrag" for the whole family.

**Core**
The mdrag service that stores and serves Documents, validates registered kinds and exposes one
ingest path. It does not fetch, schedule or host runs.

**Satellite**
A first-party repository that extends the core only through its public API.

**Kind**
A registered shape: a name plus a JSON Schema generated from its owner's Pydantic model. Kind names
are unique across every kind and take the form `<namespace>/<name>`. The `mdrag/` namespace is
reserved for built-in kinds.
_Avoid_: `content_class`, "form", "type" for this concept.

**Provider**
Code that fetches data from somewhere and sends it to the core through the ingest path. Inbound
only.
_Avoid_: "collector" for a single provider; "provider" for anything that sends data out.

**Feed factory**
The satellite framework that runs providers on a schedule or on demand and returns a handle to
poll.

**Ingest path**
The one public API every producer writes through, first-party or not. No producer has a private
route to the store.

**mdrag workflow**
An automation built on mdrag that reads Collections and writes Annotations through the API. It runs
outside the core.
_Avoid_: "knowledge app".

**Run**
One execution of an mdrag workflow, started by a schedule, an event, a request or a person steering
an app.

**App**
A packaged experience built on workflows, used by people.
_Avoid_: "knowledge app" (informal only).

**Provisional account**
An account an app creates for a visitor who has not signed up, which the visitor can claim later
with nothing lost.
_Avoid_: "anonymous user", "JIT account".
