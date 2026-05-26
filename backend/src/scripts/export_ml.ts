import { db } from '../db/schema';
import fs from 'fs';
import path from 'path';

interface Match {
  id: number;
  utcDate: string;
  status: string;
  matchday: number;
  competitionId: number;
  season: number;
  homeTeamId: number;
  awayTeamId: number;
  homeScore: number;
  awayScore: number;
}

function stdDev(values: number[]) {
  if (values.length === 0) return 0;
  const avg = values.reduce((a, b) => a + b, 0) / values.length;
  const squareDiffs = values.map(v => Math.pow(v - avg, 2));
  const avgSquareDiff = squareDiffs.reduce((a, b) => a + b, 0) / squareDiffs.length;
  return Math.sqrt(avgSquareDiff);
}

function getVenueStats(teamId: number, isHome: boolean, beforeDate: string, limit: number) {
  const query = isHome 
    ? `SELECT * FROM matches WHERE homeTeamId = ? AND utcDate < ? AND status = 'FINISHED' ORDER BY utcDate DESC LIMIT ?`
    : `SELECT * FROM matches WHERE awayTeamId = ? AND utcDate < ? AND status = 'FINISHED' ORDER BY utcDate DESC LIMIT ?`;
  
  const matches = db.prepare(query).all(teamId, beforeDate, limit) as Match[];

  if (matches.length === 0) return { avgPoints: 0, drawRate: 0, goalsStd: 0, count: 0 };

  let points = 0;
  let draws = 0;
  const goals: number[] = [];

  matches.forEach(m => {
    const teamScore = isHome ? m.homeScore : m.awayScore;
    const oppScore = isHome ? m.awayScore : m.homeScore;
    goals.push(teamScore);

    if (teamScore > oppScore) points += 3;
    else if (teamScore === oppScore) {
      points += 1;
      draws += 1;
    }
  });

  return {
    avgPoints: points / matches.length,
    drawRate: draws / matches.length,
    goalsStd: stdDev(goals),
    count: matches.length
  };
}

function getH2HStats(homeId: number, awayId: number, beforeDate: string) {
  const matches = db.prepare(`
    SELECT * FROM matches 
    WHERE ((homeTeamId = ? AND awayTeamId = ?) OR (homeTeamId = ? AND awayTeamId = ?))
    AND utcDate < ?
    AND status = 'FINISHED'
    ORDER BY utcDate DESC
    LIMIT 2
  `).all(homeId, awayId, awayId, homeId, beforeDate) as Match[];

  if (matches.length === 0) {
    // League averages: 0.46 (H), 0.25 (D), 0.29 (A)
    return { h2h_home_wins: 0.46 * 2, h2h_draws: 0.25 * 2, h2h_away_wins: 0.29 * 2 };
  }

  let hWins = 0, draws = 0, aWins = 0;

  matches.forEach(m => {
    const isHome = m.homeTeamId === homeId;
    if (m.homeScore === m.awayScore) draws++;
    else if (isHome && m.homeScore > m.awayScore) hWins++;
    else if (!isHome && m.awayScore > m.homeScore) hWins++; // Original Home ID won as Away team
    else aWins++;
  });

  // If only 1 match found, fill the other half with averages
  if (matches.length === 1) {
    hWins += 0.46;
    draws += 0.25;
    aWins += 0.29;
  }

  return { h2h_home_wins: hWins, h2h_draws: draws, h2h_away_wins: aWins };
}

async function exportMLData() {
  console.log('Starting advanced feature engineering...');

  // Get max matchdays per season/competition for normalization
  const matchdayStats = db.prepare(`
    SELECT competitionId, season, MAX(matchday) as maxMatchday 
    FROM matches 
    GROUP BY competitionId, season
  `).all() as { competitionId: number, season: number, maxMatchday: number }[];

  const maxMatchdayMap = new Map();
  matchdayStats.forEach(s => maxMatchdayMap.set(`${s.competitionId}-${s.season}`, s.maxMatchday));

  const allFinishedMatches = db.prepare(`
    SELECT * FROM matches 
    WHERE status = 'FINISHED' 
    AND homeScore IS NOT NULL 
    AND awayScore IS NOT NULL
    ORDER BY season ASC, matchday ASC, utcDate ASC
  `).all() as Match[];

  const features: any[] = [];

  for (const m of allFinishedMatches) {
    // 1. Home Form (Last 3 Home Games) & Draw Rate / Variance (Last 5 Home Games)
    const homeStats3 = getVenueStats(m.homeTeamId, true, m.utcDate, 3);
    const homeStats5 = getVenueStats(m.homeTeamId, true, m.utcDate, 5);

    // 2. Away Form (Last 3 Away Games) & Draw Rate / Variance (Last 5 Away Games)
    const awayStats3 = getVenueStats(m.awayTeamId, false, m.utcDate, 3);
    const awayStats5 = getVenueStats(m.awayTeamId, false, m.utcDate, 5);

    // Filter: Require at least some history to be useful
    if (homeStats3.count < 3 || awayStats3.count < 3) continue;

    // 3. H2H (Last 2 meetings)
    const h2h = getH2HStats(m.homeTeamId, m.awayTeamId, m.utcDate);

    // 4. Season Stage (Matchday / Max Matchday)
    const maxMd = maxMatchdayMap.get(`${m.competitionId}-${m.season}`) || 38;
    const season_stage = m.matchday / maxMd;

    const target = m.homeScore > m.awayScore ? 0 : (m.homeScore === m.awayScore ? 1 : 2);

    features.push({
      match_id: m.id,
      season: m.season,
      matchday: m.matchday,
      season_stage: season_stage.toFixed(3),
      home_id: m.homeTeamId,
      away_id: m.awayTeamId,
      home_form_home: homeStats3.avgPoints.toFixed(2),
      away_form_away: awayStats3.avgPoints.toFixed(2),
      draw_rate_home: homeStats5.drawRate.toFixed(2),
      draw_rate_away: awayStats5.drawRate.toFixed(2),
      home_goals_std: homeStats5.goalsStd.toFixed(2),
      away_goals_std: awayStats5.goalsStd.toFixed(2),
      h2h_home_wins: h2h.h2h_home_wins.toFixed(2),
      h2h_draws: h2h.h2h_draws.toFixed(2),
      h2h_away_wins: h2h.h2h_away_wins.toFixed(2),
      target: target
    });
  }

  const csvHeader = Object.keys(features[0]).join(',');
  const csvRows = features.map(f => Object.values(f).join(','));
  const csvContent = [csvHeader, ...csvRows].join('\n');

  const outputPath = path.join(__dirname, '../../data/football_features.csv');
  fs.writeFileSync(outputPath, csvContent);

  console.log(`Exported ${features.length} matches with advanced features to ${outputPath}`);
}

exportMLData().catch(console.error);
