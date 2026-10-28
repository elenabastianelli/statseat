# Roadmap

What we would build next, from the demo (one synthetic match, one container) to a product used by clubs.

## Next: better data and richer moments

- **A more realistic generator.** A new movement model for the players, to fix the steps in their accelerations and the slow passes that the realism report marks red ([METHODS.md](METHODS.md#2-how-realistic-it-is)).
- **2D replays of the key moments**, drawn from the tracking: VAR already picks the moments and their frames.
- **Video replays** linked to the timeline, with whoever holds the rights.
- **Tactician and Linesman as Foundry agents**: today they are deterministic code in the generator.
- **The quiz after the match** as well as at half-time.

## The stadium experience

- **Real-time updates** with server-sent events instead of polling.
- **The stadium's own sound:** the roar of the crowd from the stadium microphones, as a signal for the key moments.
- **The big screen:** the same moments and charts on the stadium screen.
- **"Hey Archie"**, a voice assistant with Azure AI Speech.
- **Arabic**, with a right-to-left interface.

## Production for a 60,000-seat stadium

| Need | Azure service |
|---|---|
| The official live feed | Event Hubs, Fabric Real-Time Intelligence |
| Sessions, favourites, points | Cosmos DB |
| Live updates to every seat | Web PubSub |
| Secrets for partner systems | Key Vault |
| Scale and resilience | Several Container Apps replicas, load tests |
| Monitoring and cost | Application Insights dashboards and budgets |

## For clubs and fans

- **Club ticketing and login:** the app opens from the club's own ticket and account.
- **Loyalty programme:** the points of the games become real rewards.
- **Many clubs:** one configuration per club, competition and stadium.
- **An anonymous dashboard for the club:** what fans ask and look at during the match.
