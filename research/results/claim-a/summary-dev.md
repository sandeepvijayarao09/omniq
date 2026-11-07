# Results · split `dev`

| Condition | n | Accuracy (answer) | Leak rate | Exposure | Abstain recall | Abstain precision | Latency |
|---|---|---|---|---|---|---|---|
| B3 | 112 | 58.3% | 0.0% | 0.0% | 100.0% | 50.9% | 1.7s |
| O2 | 112 | 92.9% | 0.0% | 0.0% | 100.0% | 96.4% | 1.8s |
| O3 | 112 | 96.4% | 0.0% | 0.0% | 100.0% | 96.4% | 3.5s |

**Accuracy by question type**

| Condition | aggregation | cross_source | multi_hop | permission_probe | property_probe | relational | single_fact |
|---|---|---|---|---|---|---|---|
| B3 | 0.0% | 100.0% | 94.1% | 0.0% | 50.0% | 59.5% | 31.2% |
| O2 | 0.0% | 100.0% | 94.1% | 100.0% | 100.0% | 100.0% | 87.5% |
| O3 | 100.0% | 100.0% | 94.1% | 100.0% | 100.0% | 100.0% | 87.5% |

**Paired against B3** (answer cases both conditions ran)

| Condition | pairs | Δ accuracy | 95% CI | McNemar p |
|---|---|---|---|---|
| O2 | 84 | +34.5 pts | [+25.0, +45.2] | 3.73e-09 |
| O3 | 84 | +38.1 pts | [+28.6, +48.8] | 4.66e-10 |

**Environments**

| Condition | git | model | embedder | environment | benchmark |
|---|---|---|---|---|---|
| B3 | aab6cd7 | local-gemma | ollama:hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest | 0257294ac338e134 | f16394b7da32 |
| O2 | aab6cd7 | local-gemma | ollama:hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest | 0257294ac338e134 | f16394b7da32 |
| O3 | aab6cd7 | local-gemma | ollama:hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest | 0257294ac338e134 | f16394b7da32 |
