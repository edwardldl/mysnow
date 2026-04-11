"use client";

import React, { useState } from "react";
import { Calendar, Search, History, Trash2, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import { fetchHistoricalWeatherData } from "@/lib/api";
import { blendForecasts, groupData } from "@/lib/data";
import { Location, DayData } from "@/lib/types";

interface HistorySectionProps {
  currentLocationId: string;
}

export default function HistorySection({ currentLocationId }: HistorySectionProps) {
  const [startDate, setStartDate] = useState("2024-02-01");
  const [endDate, setEndDate] = useState("2024-02-15");
  const [isLoading, setIsLoading] = useState(false);
  const [historyData, setHistoryData] = useState<DayData[]>([]);
  const [error, setError] = useState<string | null>(null);

  const handleFetch = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchHistoricalWeatherData(currentLocationId, startDate, endDate);
      const blended = blendForecasts(null, data.ecmwfData, data.location, "hybrid", "historical");
      const grouped = groupData(blended);
      setHistoryData(grouped);
    } catch (err: any) {
      setError(err.message || "Failed to fetch historical data");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-8 mt-12 flex flex-col gap-8 pb-20">
      <div className="flex flex-col gap-2">
        <h2 className="text-3xl font-black text-white tracking-tight">History Backtest</h2>
        <p className="text-slate-400 text-sm">Compare historical snowfall across different periods.</p>
      </div>

      <div className="glass-panel p-6 rounded-3xl grid grid-cols-1 md:grid-cols-3 gap-6 items-end border border-white/10">
        <div className="flex flex-col gap-2">
          <label className="text-[10px] uppercase tracking-widest text-slate-500 font-bold ml-1 flex items-center gap-1.5">
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
          <label className="text-[10px] uppercase tracking-widest text-slate-500 font-bold ml-1 flex items-center gap-1.5">
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
          className="h-[46px] bg-accent-rose hover:bg-rose-500 text-white rounded-xl shadow-lg shadow-rose-500/20 transition-all font-bold text-sm flex items-center justify-center gap-2"
        >
          {isLoading ? <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <History className="w-5 h-5" />}
          Run Backtest
        </button>
      </div>

      {error && (
        <div className="glass-panel p-6 rounded-2xl border-accent-rose/20 text-accent-rose text-sm font-bold">
          {error}
        </div>
      )}

      {historyData.length > 0 && (
        <div className="glass-panel rounded-3xl overflow-hidden border border-white/10 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <div className="overflow-x-auto no-scrollbar">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-white/5 text-[10px] uppercase tracking-widest font-black text-slate-500">
                  <th className="px-6 py-4">Date</th>
                  <th className="px-6 py-4">Liquid (mm)</th>
                  <th className="px-6 py-4">Snowfall (cm)</th>
                  <th className="px-6 py-4">Snow Depth</th>
                  <th className="px-6 py-4">Avg Temp</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {historyData.map((day) => (
                  <tr key={day.dateStr} className="hover:bg-white/[0.02] transition-colors group">
                    <td className="px-6 py-4">
                      <div className="flex flex-col">
                        <span className="text-sm font-bold text-white uppercase">{new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(new Date(day.dateStr))}</span>
                        <span className="text-[10px] text-slate-500">{day.dateStr}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span className="text-sm font-black text-blue-400">{day.totalPrecipitation.toFixed(1)}</span>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                         <span className={cn("text-lg font-black", day.totalSnowfall > 0 ? "text-accent-cyan" : "text-slate-700")}>
                           {day.totalSnowfall.toFixed(1)}
                         </span>
                         {day.totalSnowfall >= 10 && (
                           <span className="text-[8px] px-1.5 py-0.5 rounded bg-accent-cyan/10 text-accent-cyan font-black border border-accent-cyan/20">EVENT</span>
                         )}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span className="text-sm font-bold text-slate-400">{day.snowDepth}</span>
                    </td>
                    <td className="px-6 py-4">
                       <span className={cn(
                         "text-sm font-bold",
                         (day.windows[4]?.avgTemp ?? 0) <= 0 ? "text-blue-400" : "text-rose-400"
                       )}>
                         {(day.windows[4]?.avgTemp ?? 0).toFixed(1)}°C
                       </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
