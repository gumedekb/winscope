import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Match, Team, Prediction } from '../types';
import { api } from '../services/api';
import { PredictionBar } from '../components/PredictionBar';
import { ArrowLeft, Trophy, History, Users, Loader2 } from 'lucide-react';

export const MatchDetail: React.FC = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [match, setMatch] = useState<Match | null>(null);
  const [h2h, setH2h] = useState<Match[]>([]);
  const [homeForm, setHomeForm] = useState<Match[]>([]);
  const [awayForm, setAwayForm] = useState<Match[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        // Using current season for data fetch
        const matches = await api.getMatches({ season: 2025 });
        const currentMatch = matches.find(m => m.id === Number(id));
        
        if (currentMatch) {
          const prediction = await api.getPrediction(currentMatch);
          setMatch({
            ...currentMatch,
            prediction
          });

          // H2H (Last 3 seasons)
          const history24 = await api.getMatches({ season: 2024 });
          const history23 = await api.getMatches({ season: 2023 });
          const allHistory = [...matches, ...history24, ...history23];

          const h2hMatches = allHistory.filter(m => 
            m.status === 'FINISHED' &&
            ((m.homeTeamId === currentMatch.homeTeamId && m.awayTeamId === currentMatch.awayTeamId) ||
             (m.homeTeamId === currentMatch.awayTeamId && m.awayTeamId === currentMatch.homeTeamId))
          ).sort((a, b) => new Date(b.utcDate).getTime() - new Date(a.utcDate).getTime());
          setH2h(h2hMatches);

          // Recent Form (Last 5)
          const hForm = matches.filter(m => 
            m.status === 'FINISHED' && (m.homeTeamId === currentMatch.homeTeamId || m.awayTeamId === currentMatch.homeTeamId)
            && new Date(m.utcDate) < new Date(currentMatch.utcDate)
          ).sort((a, b) => new Date(b.utcDate).getTime() - new Date(a.utcDate).getTime()).slice(0, 5);
          setHomeForm(hForm);

          const aForm = matches.filter(m => 
            m.status === 'FINISHED' && (m.homeTeamId === currentMatch.awayTeamId || m.awayTeamId === currentMatch.awayTeamId)
            && new Date(m.utcDate) < new Date(currentMatch.utcDate)
          ).sort((a, b) => new Date(b.utcDate).getTime() - new Date(a.utcDate).getTime()).slice(0, 5);
          setAwayForm(aForm);
        }
      } catch (err) {
        console.error('Failed to fetch match details:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [id]);

  if (loading) return (
    <div className="flex flex-col items-center justify-center min-h-screen text-white">
      <Loader2 className="w-12 h-12 text-accent animate-spin mb-4" />
      <p className="font-bold text-xl">Loading analysis...</p>
    </div>
  );

  if (!match) return <div>Match not found</div>;

  return (
    <div className="max-w-7xl mx-auto w-full px-4 py-8">
      <button 
        onClick={() => navigate('/')}
        className="flex items-center gap-2 text-gray-400 hover:text-white mb-8 transition-colors font-bold uppercase text-xs"
      >
        <ArrowLeft className="w-4 h-4" /> Back to matches
      </button>

      {/* Header Card */}
      <div className="glass-morphism rounded-3xl p-8 mb-8 relative overflow-hidden">
        <div className="absolute top-0 right-0 p-4">
           <span className="bg-accent/20 text-accent px-3 py-1 rounded-full text-xs font-bold uppercase tracking-widest">Prediction Engine v4.0</span>
        </div>

        <div className="flex flex-col md:flex-row items-center justify-between gap-12 relative z-10">
          <div className="flex flex-col items-center flex-1">
            <img src={api.getTeamCrestUrl(match.homeTeamId)} className="w-32 h-32 object-contain mb-4 drop-shadow-2xl" alt="home" />
            <h2 className="text-3xl font-black text-white text-center">{match.homeTeam?.shortName || match.homeTeamId}</h2>
            {match.prediction?.home_elo && (
              <span className="text-gray-500 font-bold mt-2">ELO: {Math.round(match.prediction.home_elo)}</span>
            )}
          </div>

          <div className="flex flex-col items-center gap-4">
            <div className="text-gray-500 font-black text-5xl italic opacity-30">VS</div>
            <div className="text-center">
                <p className="text-accent font-bold uppercase tracking-widest text-sm">{new Date(match.utcDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</p>
                <p className="text-white font-black text-2xl">{new Date(match.utcDate).toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Johannesburg' })}</p>
            </div>
          </div>

          <div className="flex flex-col items-center flex-1">
            <img src={api.getTeamCrestUrl(match.awayTeamId)} className="w-32 h-32 object-contain mb-4 drop-shadow-2xl" alt="away" />
            <h2 className="text-3xl font-black text-white text-center">{match.awayTeam?.shortName || match.awayTeamId}</h2>
            {match.prediction?.away_elo && (
              <span className="text-gray-500 font-bold mt-2">ELO: {Math.round(match.prediction.away_elo)}</span>
            )}
          </div>
        </div>

        <div className="mt-12 max-w-2xl mx-auto bg-white/5 rounded-2xl p-6 border border-white/10">
            <h3 className="text-center text-gray-400 font-bold uppercase text-xs mb-4 tracking-widest flex items-center justify-center gap-2">
               ✨ AI Analysis
            </h3>
            {match.prediction && (
              <PredictionBar h={match.prediction.home_win} d={match.prediction.draw} a={match.prediction.away_win} />
            )}
            <div className="mt-4 text-center">
                <p className="text-white font-bold text-lg">
                    Expected Outcome: <span className="text-accent">{match.prediction?.outcome === 'H' ? 'Home Win' : (match.prediction?.outcome === 'D' ? 'Draw' : 'Away Win')}</span>
                </p>
            </div>
        </div>
      </div>

      {/* Info Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Column 1: Home Form */}
        <div className="flex flex-col gap-6">
           <h3 className="text-white font-black uppercase flex items-center gap-2 text-sm border-b border-white/10 pb-2">
              <History className="w-4 h-4 text-accent" /> Recent Form ({match.homeTeam?.shortName || 'Home'})
           </h3>
           <div className="flex flex-col gap-3">
              {homeForm.map(m => (
                <div key={m.id} className="glass-morphism p-4 rounded-xl flex justify-between items-center text-xs">
                    <span className="font-bold text-gray-400">{new Date(m.utcDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</span>
                    <div className="flex items-center gap-2">
                        <span className="text-white font-bold">{m.homeTeamId === match.homeTeamId ? 'HOME' : 'AWAY'}</span>
                        <span className={`px-2 py-1 rounded font-black ${((m.homeScore! > m.awayScore! && m.homeTeamId === match.homeTeamId) || (m.awayScore! > m.homeScore! && m.awayTeamId === match.homeTeamId)) ? 'bg-green-500/20 text-green-500' : (m.homeScore === m.awayScore ? 'bg-yellow-500/20 text-yellow-500' : 'bg-red-500/20 text-red-500')}`}>
                            {m.homeScore} - {m.awayScore}
                        </span>
                    </div>
                </div>
              ))}
           </div>
        </div>

        {/* Column 2: H2H */}
        <div className="flex flex-col gap-6">
           <h3 className="text-white font-black uppercase flex items-center gap-2 text-sm border-b border-white/10 pb-2">
              <Users className="w-4 h-4 text-accent" /> Head to Head
           </h3>
           <div className="flex flex-col gap-3">
              {h2h.map(m => (
                <div key={m.id} className="glass-morphism p-4 rounded-xl flex flex-col gap-2 text-xs">
                    <div className="flex justify-between items-center">
                       <span className="text-gray-500 font-bold italic">{new Date(m.utcDate).getFullYear()}</span>
                       <span className="text-white font-black">{m.homeScore} - {m.awayScore}</span>
                    </div>
                    <div className="flex justify-between text-[10px] text-gray-400 font-medium">
                        <span>{m.homeTeamId === match.homeTeamId ? (match.homeTeam?.shortName || 'Home') : (match.awayTeam?.shortName || 'Away')}</span>
                        <span>{m.awayTeamId === match.awayTeamId ? (match.awayTeam?.shortName || 'Away') : (match.homeTeam?.shortName || 'Home')}</span>
                    </div>
                </div>
              ))}
              {h2h.length === 0 && <p className="text-gray-500 text-center py-10 font-bold italic">No recent head-to-head records</p>}
           </div>
        </div>

        {/* Column 3: Away Form */}
        <div className="flex flex-col gap-6">
           <h3 className="text-white font-black uppercase flex items-center gap-2 text-sm border-b border-white/10 pb-2">
              <History className="w-4 h-4 text-accent" /> Recent Form ({match.awayTeam?.shortName || 'Away'})
           </h3>
           <div className="flex flex-col gap-3">
              {awayForm.map(m => (
                <div key={m.id} className="glass-morphism p-4 rounded-xl flex justify-between items-center text-xs">
                    <span className="font-bold text-gray-400">{new Date(m.utcDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</span>
                    <div className="flex items-center gap-2">
                        <span className="text-white font-bold">{m.homeTeamId === match.awayTeamId ? 'HOME' : 'AWAY'}</span>
                        <span className={`px-2 py-1 rounded font-black ${((m.homeScore! > m.awayScore! && m.homeTeamId === match.awayTeamId) || (m.awayScore! > m.homeScore! && m.awayTeamId === match.awayTeamId)) ? 'bg-green-500/20 text-green-500' : (m.homeScore === m.awayScore ? 'bg-yellow-500/20 text-yellow-500' : 'bg-red-500/20 text-red-500')}`}>
                            {m.homeScore} - {m.awayScore}
                        </span>
                    </div>
                </div>
              ))}
           </div>
        </div>
      </div>
    </div>
  );
};
