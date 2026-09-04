"use client";
import React from 'react';

interface Props {
  h: number;
  d: number;
  a: number;
}

export const PredictionBar: React.FC<Props> = ({ h, d, a }) => {
  return (
    <div className="w-full mt-2">
      <div className="flex h-3 w-full rounded-full overflow-hidden bg-gray-800">
        <div 
          className="bg-green-500 h-full transition-all duration-500" 
          style={{ width: `${h * 100}%` }}
        />
        <div 
          className="bg-yellow-500 h-full transition-all duration-500" 
          style={{ width: `${d * 100}%` }}
        />
        <div 
          className="bg-red-500 h-full transition-all duration-500" 
          style={{ width: `${a * 100}%` }}
        />
      </div>
      <div className="flex justify-between text-[10px] mt-1 font-bold text-gray-400">
        <span>H: {(h * 100).toFixed(0)}%</span>
        <span>D: {(d * 100).toFixed(0)}%</span>
        <span>A: {(a * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
};
