import React, { useEffect, useState } from 'react';
import { Match } from '../types';
import { api } from '../services/api';
import { MatchCard } from '../components/MatchCard';
import { Calendar, History, Loader2, RefreshCw } from 'lucide-react';
import logo from '../assets/winscope.png';

export const Dashboard: React.FC = () => {
  const [matches, setMatches] = useState<Match[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const fetchData = async () => {
    try {
      setLoading(true);
      const now = new Date();
      const threeDaysFromNow = new Date();
      threeDaysFromNow.setDate(now.getDate() + 3);

      // Get upcoming matches from the dedicated upcoming DB
      const upcoming = await api.getUpcomingMatchesList();
      
      const filtered = upcoming
        .filter(m => {
          const date = new Date(m.utcDate);
          return date >= now && date <= threeDaysFromNow;
        })
        .sort((a, b) => new Date(a.utcDate).getTime() - new Date(b.utcDate).getTime());

      // Get predictions for each in small batches to avoid CPU spikes
      const enriched: Match[] = [];
      const batchSize = 3;
      for (let i = 0; i < filtered.length; i += batchSize) {
        const batch = filtered.slice(i, i + batchSize);
        const batchResults = await Promise.all(batch.map(async m => {
          try {
            const prediction = await api.getPrediction(m);
            return { ...m, prediction };
          } catch (e) {
            console.error(`Prediction failed for match ${m.id}`, e);
            return m;
          }
        }));
        enriched.push(...batchResults);
      }

      setMatches(enriched);
    } catch (err) {
      console.error('Failed to fetch dashboard data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleFetchUpcoming = async () => {
    try {
      setFetching(true);
      await api.fetchUpcomingMatches();
      await fetchData();
    } catch (err) {
      console.error('Failed to fetch upcoming matches:', err);
    } finally {
      setFetching(false);
    }
  };

  const handleSyncResults = async () => {
    try {
      setSyncing(true);
      const res = await api.syncHistoricalResults();
      alert(res.message);
    } catch (err) {
      console.error('Failed to sync results:', err);
      alert('Failed to sync results. Check console.');
    } finally {
      setSyncing(false);
    }
  };

  const groupByDate = (matches: Match[]) => {
    return matches.reduce((groups, match) => {
      const date = new Date(match.utcDate).toLocaleDateString('en-GB', { 
        weekday: 'long', day: 'numeric', month: 'long' 
      });
      if (!groups[date]) groups[date] = [];
      groups[date].push(match);
      return groups;
    }, {} as Record<string, Match[]>);
  };

  const grouped = groupByDate(matches);

  if (loading && matches.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-white">
        <Loader2 className="w-12 h-12 text-accent animate-spin mb-4" />
        <p className="font-bold text-xl animate-pulse">Analysing match data...</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto w-full px-4 py-8">
      <header className="mb-10 text-center relative">
        <img src={logo} alt="WinScope Logo" className="h-70 mx-auto mb-4" />
        <p className="text-gray-400 font-medium">Smart Football Predictions Powered by AI</p>

        <div className="flex flex-wrap items-center justify-center gap-4 mt-6">
          <button 
            onClick={handleFetchUpcoming}
            disabled={fetching}
            className="bg-accent hover:bg-accent/80 disabled:opacity-50 text-white font-bold py-2 px-6 rounded-full flex items-center gap-2 transition-all shadow-lg"
          >
            {fetching ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Fetch Upcoming (3 Days)
          </button>

          <button 
            onClick={handleSyncResults}
            disabled={syncing}
            className="bg-white/10 hover:bg-white/20 disabled:opacity-50 text-white font-bold py-2 px-6 rounded-full flex items-center gap-2 transition-all border border-white/10"
          >
            {syncing ? <Loader2 className="w-4 h-4 animate-spin" /> : <History className="w-4 h-4" />}
            Sync Latest Results
          </button>
        </div>
      </header>

      {matches.length === 0 ? (
        <div className="text-center py-20 glass-morphism rounded-3xl">
          <Calendar className="w-16 h-16 text-gray-600 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-gray-400">No upcoming matches found</h2>
          <p className="text-gray-500 mt-2">Click the button above to fetch new data from API-Sports</p>
        </div>
      ) : (
        Object.entries(grouped).map(([date, dayMatches]) => (
          <div key={date} className="mb-10">
            <h2 className="text-lg font-black text-white mb-4 border-l-4 border-accent pl-4 flex items-center gap-2">
               <Calendar className="w-5 h-5 text-accent" />
               {date}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {dayMatches.map(match => (
                <MatchCard 
                  key={match.id} 
                  match={match} 
                  prediction={match.prediction}
                />
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
};
