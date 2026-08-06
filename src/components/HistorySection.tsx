"use client";

import React, { useEffect, useRef, useState } from "react";
import { Calendar, History } from "lucide-react";
import { fetchHistoricalWeatherData } from "@/lib/api";
import { blendForecasts, groupData } from "@/lib/data";
import { DayData } from "@/lib/types";

interface HistorySectionProps {
  currentLocationId: string;
  algoId: string;
  elevationMode: string;
  onResults: (locationId: string, days: DayData[]) => void;
}

export default function HistorySection({ currentLocationId, algoId, elevationMode, onResults }: HistorySectionProps) {
  const [startDate, setStartDate] = useState("2024-11-01");
  const [endDate, setEndDate] = useState("2024-11-15");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestRef = useRef(0);

  useEffect(() => {
    // Ignore an in-flight result for the previous location.
    latestRequestRef.current += 1;
    setIsLoading(false);
    setError(null);
  }, [currentLocationId]);

  const handleFetch = async () => {
    if (!startDate || !endDate || startDate > endDate) {
      setError("Choose a start date that is on or before the end date.");
      return;
    }

    const requestId = ++latestRequestRef.current;
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchHistoricalWeatherData(currentLocationId, startDate, endDate, 'ecmwf_ifs', elevationMode);
      const blended = blendForecasts(null, data.ecmwfData, data.location, algoId, "historical");
      const grouped = groupData(blended);

      if (requestId !== latestRequestRef.current) return;
      onResults(currentLocationId, grouped);
    } catch (err: unknown) {
      if (requestId !== latestRequestRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to fetch historical data");
    } finally {
      if (requestId === latestRequestRef.current) {
        setIsLoading(false);
      }
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-8 mt-12 flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h2 className="text-3xl font-black text-white tracking-tight">History Backtest</h2>
        <p className="text-slate-400 text-sm italic">Analyze explicit-model reference-site snowfall; this is not managed-piste history.</p>
      </div>

      <div className="glass-panel p-6 rounded-3xl grid grid-cols-1 md:grid-cols-3 gap-6 items-end border border-white/10">
        <div className="flex flex-col gap-2">
          <label className="text-[10px] uppercase font-black tracking-widest text-slate-500 ml-1 flex items-center gap-1.5">
            <Calendar className="w-3 h-3" />
            Start Date
          </label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-xl py-3 px-4 text-sm text-white focus:outline-none focus:border-accent-rose/50 focus:bg-white/[0.07] transition-all"
          />
        </div>
        <div className="flex flex-col gap-2">
          <label className="text-[10px] uppercase font-black tracking-widest text-slate-500 ml-1 flex items-center gap-1.5">
            <Calendar className="w-3 h-3" />
            End Date
          </label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-xl py-3 px-4 text-sm text-white focus:outline-none focus:border-accent-rose/50 focus:bg-white/[0.07] transition-all"
          />
        </div>
        <button
          onClick={handleFetch}
          disabled={isLoading}
          className="h-[46px] bg-accent-rose hover:bg-rose-500 text-white rounded-xl shadow-lg shadow-rose-500/20 transition-all font-bold text-sm flex items-center justify-center gap-2 group"
        >
          {isLoading ? <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <History className="w-5 h-5 group-hover:rotate-[-45deg] transition-transform" />}
          Run Backtest
        </button>
      </div>

      {error && (
        <div className="glass-panel p-6 rounded-2xl border-accent-rose/20 text-accent-rose text-sm font-bold animate-in fade-in slide-in-from-top-2">
          {error}
        </div>
      )}
    </div>
  );
}
