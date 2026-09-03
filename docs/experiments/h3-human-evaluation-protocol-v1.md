# H3 blinded human-evaluation protocol v1

Study status: `PREPARED_NOT_EXECUTED`  
Ethics or institutional review status:
`TO_BE_CONFIRMED_BY_RESEARCHER_BEFORE_DATA_COLLECTION`

## Objective and scope

The study evaluates the quality of explanations for a fixed RCA result. It does
not ask reviewers to decide whether the root cause itself is correct. Each item
presents one frozen explanation and the already-blinded supporting context.
Explanations were produced under different information-presentation conditions,
whose identities remain hidden until ratings have been validated and locked.

The frozen population contains 120 items: 40 incidents and three explanation
conditions. Six pseudonymous reviewer slots (`R01`–`R06`) each receive 40 items,
exactly one item for every incident. Each item is independently rated twice.
Assignment is deterministic from seed 42. Every reviewer's exposure across the
three hidden conditions is a permutation of 14/13/13.

## Procedure

Reviewers use only the explanation and supporting context in their packet. They
must not use external tools or LLMs, attempt to infer the hidden condition, or
discuss item scores before submitting. Each of the five fields must receive an
integer from 1 through 5 according to the frozen rubric. No direct identifiers
are collected; optional metadata uses only the provided categorical template.

The researcher validates all returned packets against the private assignment
map. Before any condition unblinding or analysis, the complete merged ratings
file is checksummed and that hash is recorded in the ratings lock. Invalid,
incomplete, duplicate, out-of-range, decimal, blank, or unassigned rows are
rejected fail-closed.

## Blinding and custody

Reviewer packets contain no incident, run, provider-response, condition, ground
truth, dataset, or source-system identity. The private condition and assignment
maps are not distributed. The text suffix `-generic` in Linux telemetry is
ordinary context and is not an experimental condition label.

## Participation notice template

This template must be adapted to applicable institutional rules before data
collection and is not a claim of ethics/IRB/CEP approval:

> You are invited to evaluate software incident explanations for research.
> Participation is voluntary. You may stop before submitting your packet.
> Do not enter names, contact details, employer information, or other direct
> identifiers. Your pseudonymous ratings will be analyzed in aggregate. Contact,
> retention, risk, withdrawal, and oversight details must be supplied by the
> researcher under the institution's approved procedure before recruitment.
