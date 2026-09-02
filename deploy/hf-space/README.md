---
title: Neural KG
emoji: 🧭
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: Ask US public data in plain English, over ARD
---

# Neural KG

Ask a question in plain English about US public data. An **ARD Agent Finder** discovers which
dataset can answer it, a planner checks the source can answer it *before* touching the network,
a single generic accessor fetches it live, and the answer is validated against the question —
backtracking to the next candidate when the wrong table was picked.

There is **no per-source query code**. Every source is described once as an
[OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) document with an
`access:` extension saying how to query it, and discovery happens over
[ARD](https://github.com/ards-project/ard-spec).

Try: *Is the American Red Cross a 501(c)(3)?* · *Apple total revenue from 2019 to 2023* ·
*Which universities get more than a billion dollars a year from NIH?*

## What this Space runs

Two processes in one container. The Agent Finder owns the ARD index and answers discovery on an
internal port; only the harness is exposed on 7860.

The engine holds no knowledge of any particular source — that is the design, and
`tests/test_query_understanding_isolation.py` enforces it by failing if a source name ever
appears in a query-understanding prompt. The corpus in this image is therefore an *input*: swap
`sources/`, `corpora/` and `catalog/` and the same engine answers over a different ARD.

Source: [github.com/rvguha/NeuralKG](https://github.com/rvguha/NeuralKG)

## Required secrets

Set these under **Settings → Variables and secrets**:

| Secret | Why |
|---|---|
| `OPENROUTER_API_KEY` | chat, re-ranking and synthesis |
| `OPENAI_API_KEY` | query embeddings — must be the model that built the shipped index |
| `CENSUS_API_KEY` | optional; Census sources |
| `DATA_GOV_API_KEY` | optional; College Scorecard and other api.data.gov sources |

Chat and embeddings are deliberately separate providers: the shipped index was built with an
OpenAI embedding model, and a query embedded by a different model is not comparable to it.

BigQuery-backed sources are omitted — they need a service-account credential that a public Space
has no safe way to hold. The engine refuses a source it cannot authenticate rather than guessing.
