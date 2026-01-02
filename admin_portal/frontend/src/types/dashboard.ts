export interface DashboardStats {
  total_api_keys: number;
  active_api_keys: number;
  revoked_api_keys: number;
  total_budget: number;
  total_budget_used: number;
  total_models: number;
  active_models: number;
  system_status: string;
  new_keys_this_week: number;
  // Models that have usage but no pricing configured
  models_without_pricing: string[];
}
