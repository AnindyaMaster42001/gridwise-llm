# Fayek's deployment and submission runbook

Do the implementation/integration release gate first. Public hosting and registry
access have not yet been selected; this runbook does not claim deployment.

1. Merge all lanes. Run `python -m pytest --integration -q`, then the live-model
   paraphrase suite using the README. Set `ALLOW_NO_LLM=0`. Both keys remain in
   the host's secret store or an ignored `.env`.
2. Run `python -m harness.preflight --history`. Resolve stubs and secret findings.
   Missing submission fields remain blockers until recorded below.
3. On the chosen Docker host, build the image and run it with the README command.
   Use a continuously running host, expose the configured port, and provide an
   unauthenticated HTTP(S) route for both required endpoints.
4. Run the public judge three times from a different network. Save the JSON;
   check actual model execution, not only fallback output. Repeat the synthetic
   stress pack and live paraphrases. Measure a fresh process before a warm repeat.
5. Publish a version tied to the verified commit. Bash example:

```bash
GRIDWISE_TAG="your-registry/your-namespace/gridwise-llm:$(git rev-parse --short HEAD)"
docker tag gridwise-llm:lane-d "$GRIDWISE_TAG"
docker push "$GRIDWISE_TAG"
docker inspect --format='{{index .RepoDigests 0}}' "$GRIDWISE_TAG"
```

PowerShell equivalent:

```powershell
$commit = git rev-parse --short HEAD
$imageTag = "your-registry/your-namespace/gridwise-llm:$commit"
docker tag gridwise-llm:lane-d $imageTag
docker push $imageTag
docker inspect --format='{{index .RepoDigests 0}}' $imageTag
```

6. Record the actual digest, URL, commit, and previous working digest in a copy of
   `deploy/submission.example.json` named `deploy/submission.json`. No secrets.
7. On a machine that did not build the image, pull **that digest**, run the exact
   documented command with runtime credentials, and check health within 60 seconds.
   Run all public cases. Record reviewer and report path. A local build alone
   is not evidence of a pullable registry image.
8. Review `docker history --no-trunc IMAGE` locally and inspect the final
   filesystem/config for secrets. Do not paste raw environment output into chat,
   reports, or the demo. Never use `docker commit` on a credential-bearing container.
9. Record the 2:50 script with real results, upload it to an organizer-accessible
   location, verify its duration and access from another account, then record the
   link. Keep source private during the event; make it public after the official
   deadline. Submit URL, repo, image digest, model/config details, and video.
10. Keep the endpoint, providers, and image accessible during judging. Monitor
    `/health` and periodically run a synthetic optimization request. Health alone
    does not detect exhausted model quota. Do not change a verified release
    without repeating the release checks.

Rollback: redeploy `previous_image_reference` with the same runtime configuration,
then run health and a real sample. Preserve the previous tag/digest through the
evaluation window. Do not delete another developer's containers or images.

Provider handoff to Ninad: Gemini native adapter with the selected model,
OpenRouter through `openai_compatible`, base URL ending `/api/v1`, model
`openrouter/free`. No per-provider retries in the deployment template; the
primary and fallback must both fit inside the pipeline's remaining deadline.
Check provider errors, rate limits, malformed JSON, timeout cancellation, and
actual fallback source labeling before recording a successful demo.
