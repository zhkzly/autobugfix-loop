# Official SWE harness research

## Sources

- SWE-bench repository: https://github.com/swe-bench/SWE-bench
- SWE-bench evaluation guide:
  https://www.swebench.com/SWE-bench/guides/evaluation/
- SWE-bench harness reference:
  https://www.swebench.com/SWE-bench/reference/harness/
- Verified dataset:
  https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified
- SWE-bench-Live repository:
  https://github.com/microsoft/SWE-bench-Live
- SWE-bench-Live project: https://swe-bench-live.github.io/
- SWE-bench paper, ICLR 2024 Oral:
  https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf
- SWE-bench-Live paper, NeurIPS 2025 Datasets and Benchmarks:
  https://openreview.net/forum?id=OGWkr7gXka

## Verified harness

The official entrypoint is:

```text
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path <predictions.jsonl> \
  --instance_ids <id> \
  --max_workers 1 \
  --run_id <id>
```

The prediction schema is `instance_id`, `model_name_or_path`, and
`model_patch`. The harness builds or pulls Docker images, applies the model
patch, executes its generated evaluation script, and writes per-instance test
output/report plus an aggregate run report. `predictions_path=gold` is the
official environment-validation path.

## Live harness

The pinned multi-language tag exposes:

```text
python -m evaluation.evaluation \
  --dataset SWE-bench-Live/MultiLang \
  --instance_ids <id> \
  --platform linux \
  --patch_dir <predictions.json> \
  --output_dir <path> \
  --workers 1 \
  --overwrite 1
```

`--patch_dir gold` is its official gold qualification path. The harness uses
the record's `docker_image` or a deterministic
`starryzhang/sweb.eval.x86_64.*` image, applies the hidden test patch and the
submitted solution patch, runs rebuild/test/print commands, parses
FAIL_TO_PASS and PASS_TO_PASS outcomes, and writes per-instance plus aggregate
JSON reports.

The Live repository main branch changed after this tag. Formal Experiment 2
must use the pinned tag and MultiLang dataset revision together; it must not
mix the old Python-only evaluation method or a future main checkout into the
same report.

## Local preflight

Observed on 2026-07-12:

- Linux amd64 under WSL2
- Docker Engine 29.6.1, API 1.55
- 10 logical CPUs
- 16,528,838,656 bytes Docker-visible memory
- approximately 915 GiB free workspace storage

The machine is suitable for serial qualification. Resource availability does
not replace per-image pull/build and gold-patch qualification.
