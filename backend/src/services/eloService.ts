import { repository } from '../db/repository';

const K = 32;
const BASE = 1000;

export const eloService = {
  getRating: (teamId: number) => {
    return repository.getElo(teamId);
  },

  updateRating: (homeId: number, awayId: number, homeGoals: number, awayGoals: number) => {
    const rH = repository.getElo(homeId);
    const rA = repository.getElo(awayId);
    
    // Calculate expected score
    const expH = 1 / (1 + Math.pow(10, (rA - (rH + 50)) / 400));
    
    let scoreH = 0.5;
    if (homeGoals > awayGoals) scoreH = 1.0;
    else if (homeGoals < awayGoals) scoreH = 0.0;
    
    const newRH = rH + K * (scoreH - expH);
    const newRA = rA + K * ((1 - scoreH) - (1 - expH));
    
    repository.updateElo(homeId, newRH);
    repository.updateElo(awayId, newRA);
    
    return { home: newRH, away: newRA };
  }
};
