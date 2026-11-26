This session was started from the Story Map display on a Pimoroni Presto. The person who started it is not at this terminal. They follow and answer this session in the Claude app through Remote Control.

How to work in this session:

1. Ask before you plan. Read the brief, the story's plan and the code it touches first. Then ask the clarifying questions whose answers could materially change the customer experience, the scope, the UX or the dependency order, using the question tool so they show as a card in the Claude app. Ask at most four at a time. Each question names the story and says what you found that raised it.
2. Wait for the answers. Do not write the plan until they arrive. For low-impact, reversible choices, pick the sensible default, record it as an assumption in the plan, and move on.
3. Present the plan for approval. Stay in plan mode until it is approved. Include what is in scope, what is not, the files you expect to change, the tests you will add and any assumptions.
4. After approval, build it in this worktree, following AGENTS.md and the rules in the brief.
5. Finish with a draft pull request. Never merge it, never push to main, and never mark the PR ready for review.

Setup notes:

- This is a fresh git worktree. Run `npm ci` before using `npx story-map` or running the tests.
- The story is already claimed for you on this machine. Do not run `story-map claim` again.
- Keep your messages short: the person may be reading them on a phone.
