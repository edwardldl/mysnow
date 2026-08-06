export interface ConfigOption {
  id: string;
  name: string;
  desc: string;
  badge?: string;
  section?: string;
}

export const MODELS: ConfigOption[] = [
  { id: "best_match", name: "Best Match (Ensemble)", desc: "AI selects the best local models. 7-15 days.", badge: "7-15D", section: "Most Used" },
  { id: "hrrr_ecmwf", name: "Blended (HRRR+ECMWF)", desc: "HRRR (3km) + ECMWF High-Res (9km). Optimal high-res blend.", badge: "0-15D", section: "Most Used" },
  { id: "hrrr", name: "NCEP HRRR", desc: "3km / USA | 0-48h. Gold standard for US mountains.", badge: "0-48H", section: "Most Used" },
  { id: "nam", name: "NCEP NAM", desc: "3km / USA | 0-84h. Precise mesoscale tracking.", badge: "0-84H", section: "Most Used" },
  { id: "nbm", name: "NCEP NBM", desc: "2.5km / USA | 0-7d. Calibrated consensus blend.", badge: "0-7D", section: "Most Used" },
  { id: "icon_global", name: "DWD ICON Global", desc: "11km / Global | 0-7d. High-quality German global model.", badge: "0-7D", section: "Most Used" },
  { id: "ecmwf", name: "ECMWF IFS HRES", desc: "9km / Global | 3-10d. Premier high-resolution global model.", badge: "3-10D", section: "Most Used" },
  { id: "ecmwf_aifs", name: "ECMWF AIFS", desc: "AI-driven high-res model (0.25°). Next-gen efficiency.", badge: "0-15D", section: "Most Used" },
  { id: "ecmwf_aifs_ensemble", name: "ECMWF AIFS Ensemble", desc: "51-member AI ensemble average. Probabilistic depth.", badge: "0-15D", section: "Other Models" },
  { id: "gem_hrdps_west", name: "GEM HRDPS West", desc: "2.5km / Canada | 0-48h. Excellent for deep Western valleys.", badge: "0-48H", section: "Other Models" },
  { id: "gem_regional", name: "GEM Regional", desc: "10km / N. Am | 1-3d. Reliable coastal storm bridge.", badge: "1-3D", section: "Other Models" },
  { id: "gfs", name: "NCEP GFS", desc: "~13km / Global | 3-14+ d. Standard US global.", badge: "3-14D", section: "Other Models" },
];

export const ALGORITHMS: ConfigOption[] = [
  { id: "kuchera", name: "Kuchera", desc: "Reliable temperature-profile baseline." },
  { id: "cobb_2011", name: "Cobb 2011", desc: "Profile-based cloud and ascent weighting; falls back to Kuchera." },
  { id: "fixed_10", name: "Fixed 10:1", desc: "Reference baseline: 1cm snow per 1mm frozen SWE." },
  { id: "open_meteo_snowfall", name: "Open-Meteo Snowfall", desc: "Model-output comparison, using its reported snowfall depth." },
];

export const ELEVATION_MODES: ConfigOption[] = [
  { id: "min", name: "Base (Min)", desc: "Simulate for the lowest point of the resort." },
  { id: "avg", name: "Mid (Avg)", desc: "Simulate for the resort median elevation." },
  { id: "max", name: "Peak (Max)", desc: "Simulate for the highest mountain peaks." },
];
