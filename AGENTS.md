# Ask Lizheng

User-facing name: 问问立正. A source-grounded AI interface over lizheng-open-context.

- Use only the explicitly public Open Context projection for retrieval and answers. Never read private personal context, messages, community content or unpublished drafts. The authorized account integration may read only the signed-in user's verified identity and Circle Founding Member tag for access checks; never include these in model context.
- Open Context remains the content owner. data/context is a disposable, pinned copy with release hashes and license attribution.
- AI answers are AI synthesis/application, never Yuzheng replying live. Guest or community ideas retain their speakers; repository syntheses do not prove his stance.
- Models select only retrieved source IDs. Server owns links, dates, timecodes and excerpts. Do not trust generated URLs or claim quotations without exact matching.
- At the user's explicit request, admitted questions submitted after the inline v1 storage notice may be saved for 30 days to improve answers. Persist only question text, created_at, model, final status and duration; use random record IDs, never associate records with accounts, email or IP. Enforce expiry and private owner access. Never persist added background, chat history, complete answers or raw model reasoning. Older clients without the notice marker are excluded. Browser conversation history is memory only. Account scope is daily3 public answers and verified Founding Member unlimited, with minimal opaque quota metadata and encrypted short-lived authentication records. A login redirect may temporarily preserve the unsent draft in tab sessionStorage; delete it on recovery. No other analytics in this scope.
- Model credentials are server-only; never put them in source, frontend, tests, screenshots or deployment env payloads.
- Public deploy, repository publication/push and upstream Open Context release require the exact reviewed payload and destination plus explicit user approval.
- Test source attribution, response validation, input bounds, unsupported questions and upstream failures. Keep deployment compatible with Builder Space: root Dockerfile, PORT, one worker, 256 MB.
