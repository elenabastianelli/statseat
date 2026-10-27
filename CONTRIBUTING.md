# Contributing

Everything in this repository is public: code, data, documentation and history. Whoever adds something, a person or an AI assistant, follows these rules.

## Ground rules

1. **Synthetic data only.** The fictional universe and the demo match are made by the generator in `generator/`. Raw tracking or event data from any provider is never committed. The only material derived from real matches is aggregate calibration values and anonymised quality reports from the open DFL/Sportec dataset (CC BY 4.0), with attribution and without player identifiers.
2. **No real people, clubs, brands or logos** in the app, the data or the media; the generator's blocklist keeps them out of the synthetic universe. No third-party music or media without permission, and nothing offensive, political or promoting alcohol, drugs or tobacco.
3. **No personal data** of contributors or anyone else: no names, email addresses, account or student IDs, in files or in commit messages.
4. **Product content only.** Planning notes, task lists and working notes stay out of the repository.
5. **No secrets.** No keys, passwords, tokens or connection strings. The app signs in to Azure with a managed identity and Microsoft Entra ID; `.env` files stay local, and `.env.example` holds placeholders only.
6. **Numbers come from code.** Statistics are computed by deterministic code, and the agents explain them: every number an agent says comes from a tool or from the match context, and the grounding validator (`app/agents/grounding.py`) checks it.
7. **One match per ticket.** Agents answer only about the match on the fan's ticket: the server takes the match from the ticket, never from the model.
8. **English only** for code, comments, documentation and commit messages. The app's translations live in `web/src/i18n/`.
9. **Tests.** Every new calculation comes with a test on synthetic data with hand-checked values. Automated tests never call cloud models.
10. **Licences.** Our code is released under the [MIT licence](LICENSE). Reused code keeps its own licence and is credited.
