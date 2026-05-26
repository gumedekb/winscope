import { apiClient } from '../api/client';
import { repository } from '../db/repository';

async function ingest() {
  const newLeagues = ['RSA'];
  const seasons = [2022, 2023, 2024, 2025];

  for (const league of newLeagues) {
    for (const season of seasons) {
      console.log(`Ingesting ${league} season ${season}...`);
      try {
        const result = await apiClient.getHistoricalMatches(league, season);
        console.log(`Source: ${result.source}, Matches: ${result.data.length}`);

        for (const m of result.data) {
          let matchData: any;

          if (result.source === 'Football-Data') {
            matchData = {
              id: m.id,
              date: m.utcDate,
              status: m.status,
              matchday: m.matchday,
              homeId: m.homeTeam.id,
              awayId: m.awayTeam.id,
              homeGoals: m.score.fullTime.home,
              awayGoals: m.score.fullTime.away,
              homeName: m.homeTeam.name,
              awayName: m.awayTeam.name
            };
          } else if (result.source === 'TheSportsDB') {
            matchData = {
              id: parseInt(m.idEvent),
              date: m.strTimestamp || m.dateEvent,
              status: m.strStatus === 'Match Finished' ? 'FINISHED' : 'TIMED',
              matchday: parseInt(m.intRound) || 0,
              homeId: parseInt(m.idHomeTeam),
              awayId: parseInt(m.idAwayTeam),
              homeGoals: parseInt(m.intHomeScore),
              awayGoals: parseInt(m.intAwayScore),
              homeName: m.strHomeTeam,
              awayName: m.strAwayTeam
            };
          } else if (result.source === 'API-Sports') {
            matchData = {
              id: m.fixture.id,
              date: m.fixture.date,
              status: m.fixture.status.short === 'FT' ? 'FINISHED' : 'TIMED',
              matchday: parseInt(m.league.round.replace(/[^0-9]/g, '')) || 0,
              homeId: m.teams.home.id,
              awayId: m.teams.away.id,
              homeGoals: m.goals.home,
              awayGoals: m.goals.away,
              homeName: m.teams.home.name,
              awayName: m.teams.away.name
            };
          }

          if (matchData) {
            // Upsert competition first
            const leagueNames: Record<string, string> = {
              'PPL': 'Liga Portugal',
              'BL1': 'Bundesliga',
              'RSA': 'Betway Premiership'
            };
            repository.upsertCompetition(0, league, leagueNames[league] || league);

            // Upsert teams next
            repository.upsertTeam(matchData.homeId, matchData.homeName, matchData.homeName, '');
            repository.upsertTeam(matchData.awayId, matchData.awayName, matchData.awayName, '');
            
            repository.upsertMatch(
              matchData.id,
              matchData.date,
              matchData.status,
              matchData.matchday,
              league,
              season,
              matchData.homeId,
              matchData.awayId,
              matchData.homeGoals,
              matchData.awayGoals
            );
          }
        }
      } catch (e: any) {
        console.error(`Failed to ingest ${league} ${season}: ${e.message}`);
      }
    }
  }
  console.log('Ingestion complete!');
}

ingest();
