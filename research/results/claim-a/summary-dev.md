# Results · split `dev`

| Condition | n | Accuracy (answer) | Leak rate | Exposure | Abstain recall | Abstain precision | Latency |
|---|---|---|---|---|---|---|---|
| B3 | 107 | 57.3% | 0.0% | 0.0% | 100.0% | 48.0% | 1.6s |
| O2 | 107 | 92.7% | 0.0% | 0.0% | 100.0% | 96.0% | 1.7s |
| O3 | 107 | 95.1% | 0.0% | 0.0% | 100.0% | 96.0% | 10.2s |

**Accuracy by question type**

| Condition | aggregation | multi_hop | permission_probe | property_probe | relational | single_fact |
|---|---|---|---|---|---|---|
| B3 | 0.0% | 94.1% | 0.0% | 50.0% | 59.5% | 31.2% |
| O2 | 0.0% | 94.1% | 100.0% | 100.0% | 100.0% | 87.5% |
| O3 | 66.7% | 94.1% | 100.0% | 100.0% | 100.0% | 87.5% |

**Paired against B3** (answer cases both conditions ran)

| Condition | pairs | Δ accuracy | 95% CI | McNemar p |
|---|---|---|---|---|
| O2 | 82 | +35.4 pts | [+25.6, +46.3] | 3.73e-09 |
| O3 | 82 | +37.8 pts | [+28.0, +48.8] | 9.31e-10 |

**Environments**

| Condition | git | model | embedder | environment | benchmark |
|---|---|---|---|---|---|
| B3 | c597467 | local-gemma | ollama:hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest | a50802a88e5dcbc9 | b93fc4546d48 |
| O2 | c597467 | local-gemma | ollama:hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest | a50802a88e5dcbc9 | b93fc4546d48 |
| O3 | a67dc6c | local-gemma | ollama:hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest | a50802a88e5dcbc9 | b93fc4546d48 |
