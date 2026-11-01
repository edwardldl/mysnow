"use client";

import React from "react";
import { BrainCircuit } from "lucide-react";
import type { ModelForecastResult, ModelWindowStatuses } from "@/lib/model-forecast";

interface ModelForecastPanelProps {
  result: ModelForecastResult | null;
  /**
   * Set only when the ENS-EB option is selected in the Physics menu: the
   * per-window model/fallback coverage for the matched catalogue point.
   * With it, the panel also renders for windows that have no model record
   * (an explicit Kuchera-fallback notice instead of silence) and shows
   * which windows are model vs fallback.
   */
  methodContext?: ModelWindowStatuses | null;
  /** The day the user has selected, for the fallback notice and strip highlight. */
  selectedDate?: string | null;
}

const SOURCE_LABEL: Record<string, string> = {
  ecmwf: "ECMWF IFS",
  hrrr: "NOAA HRRR",
  ecmwf_openmeteo: "ECMWF IFS via Open-Meteo",
};

/** Shorter per-window chip labels where the full name is too long. */
const CHIP_LABEL: Record<string, string> = {
  ecmwf_openmeteo: "ECMWF (Open-Meteo)",
};

function formatCycle(cycleUtc: string | null): string {
  if (!cycleUtc) return "";
  const date = new Date(cycleUtc);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.toISOString().slice(0, 13).replace("T", " ")}Z cycle`;
}

function fmt(value: number | null, digits = 1): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

/**
 * Labelled model panel (design SS7): shown only at catalogue resort
 * points and only in the ECMWF / HRRR source modes, including the
 * default hrrr_ecmwf blend -- the gating lives in lib/model-forecast.ts;
 * this component renders nothing without a resolved result. In blend
 * mode the source named here is the arm actually resolved for this
 * window (HRRR inside its 48 h horizon, ECMWF beyond, with fallback),
 * so the label is per-window truthful. Windows beyond the GRIB ECMWF
 * arm's step-240 ceiling resolve to the Open-Meteo third arm and are
 * labelled "ECMWF IFS via Open-Meteo" -- never plain "ECMWF IFS" --
 * with a reduced-feature note (that arm is scored without the tclw
 * feature pair, and its forecast instance is not cycle-verified
 * against the GRIB arm). Model values sit alongside the same-window
 * Kuchera comparison and never replace the production figures.
 *
 * When the ENS-EB Physics-menu method is selected, the page passes
 * methodContext: the panel then also renders on fallback windows (with
 * an explicit notice that Kuchera values are shown for that window) and
 * a per-window coverage strip, so the menu-driven day totals and this
 * panel can never silently disagree.
 */
export default function ModelForecastPanel({ result, methodContext = null, selectedDate = null }: ModelForecastPanelProps) {
  if (!result && !methodContext) return null;
  const point = result ? result.point : methodContext!.point;

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-8 mt-4">
      <div className="glass-panel rounded-3xl border border-accent-violet/30 bg-accent-violet/5 p-5 md:p-6">
        <div className="flex flex-wrap items-center gap-2 md:gap-3">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-accent-violet/20 border border-accent-violet/40 text-accent-violet text-[10px] font-black tracking-wider">
            <BrainCircuit className="w-3.5 h-3.5" />
            MODEL FORECAST
          </span>
          <span className="text-[11px] md:text-xs text-slate-300 font-semibold">
            Produced by the MySnow ENS-EB model — not the standard Kuchera estimate
          </span>
          <span className="text-[10px] md:text-[11px] text-slate-400 ml-auto">
            {result
              ? result.arm === "ecmwf_openmeteo"
                ? `${SOURCE_LABEL[result.arm]} · anchored to the ${formatCycle(result.cycleUtc)}`
                : `${SOURCE_LABEL[result.arm]} · ${formatCycle(result.cycleUtc)}`
              : "Kuchera fallback for the selected window"}
          </span>
        </div>

        {result ? (
          <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
            <div>
              <div className="text-[10px] font-black tracking-wider text-slate-400">
                MODEL SNOWFALL · {point.pointName.toUpperCase()} ({Math.round(point.elevationM)} m)
              </div>
              <div className="text-3xl md:text-4xl font-black text-white">
                {fmt(result.forecast.modelSnowfallCm)} <span className="text-base font-bold text-slate-300">cm</span>
              </div>
            </div>
            <div>
              <div className="text-[10px] font-black tracking-wider text-slate-400">MODEL SLR</div>
              <div className="text-xl md:text-2xl font-black text-slate-100">{fmt(result.forecast.modelSlr)} : 1</div>
            </div>
            <div>
              <div className="text-[10px] font-black tracking-wider text-slate-400">LIQUID (FORECAST)</div>
              <div className="text-xl md:text-2xl font-black text-slate-100">{fmt(result.forecast.liquidMm)} mm</div>
            </div>
            <div className="text-[11px] md:text-xs text-slate-400 leading-relaxed">
              Kuchera on the same forecast:{" "}
              <span className="text-slate-200 font-bold">{fmt(result.forecast.kucheraSnowfallCm)} cm</span>
              {" · "}SLR {fmt(result.forecast.kucheraSlr)} : 1
            </div>
          </div>
        ) : (
          <div className="mt-4">
            <div className="text-xl md:text-2xl font-black text-slate-100">
              Kuchera values shown for this window
            </div>
            <div className="mt-1 text-[11px] md:text-xs text-slate-400 leading-relaxed">
              No ENS-EB record for the 24 h window ending 07:00 local
              {selectedDate ? ` on ${selectedDate}` : ""} at {point.pointName} ({Math.round(point.elevationM)} m).
              Nothing is substituted silently — the standard Kuchera estimate applies to this window only,
              while windows with a model record use the model values.
            </div>
          </div>
        )}

        {result?.forecast.reducedFeatureVariant && (
          <div className="mt-3 rounded-xl border border-accent-violet/30 bg-accent-violet/10 px-3 py-2 text-[11px] md:text-xs text-violet-100/90 leading-relaxed">
            Reduced-feature variant — this window is served by the Open-Meteo arm: total column
            liquid water is not available from Open-Meteo, so the window was scored without that
            feature pair (validated NaN pass-through). Open-Meteo publishes no cycle metadata, so
            its forecast instance is not cycle-verified against the GRIB ECMWF arm, and this arm
            keeps its own evidence clock.
          </div>
        )}

        {methodContext && (
          <div className="mt-4 flex flex-wrap items-center gap-1.5" aria-label="Model coverage by window">
            <span className="text-[9px] uppercase tracking-[0.2em] text-slate-500 font-bold mr-1">
              Window coverage
            </span>
            {methodContext.windows.map((w) => (
              <span
                key={w.windowEndLocalDate}
                className={
                  "rounded-full border px-2 py-0.5 text-[10px] font-semibold " +
                  (w.status === "model"
                    ? "border-accent-violet/40 bg-accent-violet/10 text-violet-200 "
                    : "border-white/10 bg-white/5 text-slate-400 ") +
                  (selectedDate === w.windowEndLocalDate ? "ring-1 ring-white/50" : "")
                }
              >
                {w.windowEndLocalDate.slice(5)} ·{" "}
                {w.status === "model"
                  ? `Model · ${CHIP_LABEL[w.arm ?? ""] ?? SOURCE_LABEL[w.arm ?? ""]}`
                  : "Kuchera fallback"}
              </span>
            ))}
          </div>
        )}

        <div className="mt-3 text-[10px] md:text-[11px] text-slate-500">
          {result ? (
            <>
              24 h window ending 07:00 local · {point.resortName} · Experimental model output, shown for
              comparison alongside the production forecast
              {result.blended && (
                <>
                  {" "}· Blended (HRRR+ECMWF) mode: this window is served by the {SOURCE_LABEL[result.arm]}{" "}
                  model arm — the blend uses HRRR inside its 48 h horizon and ECMWF beyond it
                  {result.arm === "ecmwf_openmeteo"
                    ? ", with the Open-Meteo arm covering the long-range tail past the GRIB ceiling"
                    : ""}
                  , and each window is labelled with the source actually shown
                </>
              )}
            </>
          ) : (
            <>
              24 h window ending 07:00 local · {point.resortName} · ENS-EB model method selected —
              windows without a model record show Kuchera values and are marked as fallback
            </>
          )}
        </div>
      </div>
    </div>
  );
}
