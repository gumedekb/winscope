import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Dashboard } from './pages/Dashboard';
import { MatchDetail } from './pages/MatchDetail';

function App() {
  return (
    <Router>
      <div className="min-h-screen bg-[#0f172a]">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/match/:id" element={<MatchDetail />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
