/**
 * API Service for Admin Portal
 *
 * Handles all API requests to the backend with Bearer token authentication.
 * Token is obtained from AWS Amplify session.
 */
import { fetchAuthSession } from 'aws-amplify/auth';
import { isAmplifyConfigured } from '../config/amplify';
import type {
  ApiKey,
  ApiKeyCreate,
  ApiKeyUpdate,
  ApiKeyListResponse,
  ApiKeyUsage,
  ModelPricing,
  PricingCreate,
  PricingUpdate,
  PricingListResponse,
  DashboardStats,
} from '../types';

const API_BASE_URL = '/api';

/**
 * Get the current authentication token from Amplify session.
 * Returns null in development mode when Cognito is not configured.
 */
async function getAuthToken(): Promise<string | null> {
  if (!isAmplifyConfigured()) {
    return null;
  }

  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.toString() || null;
  } catch {
    return null;
  }
}

/**
 * Check if user is authenticated.
 */
export async function isAuthenticated(): Promise<boolean> {
  if (!isAmplifyConfigured()) {
    // Development mode - always authenticated
    return true;
  }

  const token = await getAuthToken();
  return !!token;
}

/**
 * Base fetch wrapper with Bearer token authentication.
 */
async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  // Add Bearer token if available
  const token = await getAuthToken();
  if (token) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  } else if (isAmplifyConfigured()) {
    // Cognito is configured but no token - user needs to login
    // Redirect to login page
    window.location.href = '/login';
    throw new Error('Authentication required');
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    // Handle 401 Unauthorized - redirect to login
    if (response.status === 401) {
      window.location.href = '/login';
      throw new Error('Session expired. Please login again.');
    }

    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || error.message || `HTTP error! status: ${response.status}`);
  }

  return response.json();
}

// Auth API
export const authApi = {
  /**
   * Get Cognito configuration from backend.
   */
  getConfig: async (): Promise<{
    userPoolId: string;
    userPoolClientId: string;
    region: string;
  }> => {
    const response = await fetch(`${API_BASE_URL}/auth/config`);
    if (!response.ok) {
      throw new Error('Failed to fetch auth config');
    }
    return response.json();
  },

  /**
   * Verify current session with backend.
   */
  verify: async (): Promise<{
    authenticated: boolean;
    username?: string;
    email?: string;
  }> => {
    return apiFetch('/auth/verify');
  },

  /**
   * Get current user details from backend.
   */
  me: async (): Promise<{
    user: {
      username: string;
      email?: string;
      name?: string;
    };
    token_claims?: Record<string, unknown>;
  }> => {
    return apiFetch('/auth/me');
  },
};

// Dashboard API
export const dashboardApi = {
  getStats: async (): Promise<DashboardStats> => {
    return apiFetch('/dashboard/stats');
  },
};

// API Keys API
export const apiKeysApi = {
  list: async (params?: {
    limit?: number;
    status?: string;
    search?: string;
  }): Promise<ApiKeyListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.status) searchParams.set('status', params.status);
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/keys${query ? `?${query}` : ''}`);
  },

  get: async (apiKey: string): Promise<ApiKey> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}`);
  },

  create: async (data: ApiKeyCreate): Promise<ApiKey> => {
    return apiFetch('/keys', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (apiKey: string, data: ApiKeyUpdate): Promise<ApiKey> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deactivate: async (apiKey: string): Promise<{ message: string }> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}`, {
      method: 'DELETE',
    });
  },

  reactivate: async (apiKey: string): Promise<{ message: string }> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}/reactivate`, {
      method: 'POST',
    });
  },

  deletePermanently: async (apiKey: string): Promise<{ message: string }> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}/permanent`, {
      method: 'DELETE',
    });
  },

  getUsage: async (apiKey: string): Promise<ApiKeyUsage> => {
    return apiFetch(`/keys/${encodeURIComponent(apiKey)}/usage`);
  },
};

// Pricing API
export const pricingApi = {
  list: async (params?: {
    limit?: number;
    provider?: string;
    status?: string;
    search?: string;
  }): Promise<PricingListResponse> => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.provider) searchParams.set('provider', params.provider);
    if (params?.status) searchParams.set('status', params.status);
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return apiFetch(`/pricing${query ? `?${query}` : ''}`);
  },

  getProviders: async (): Promise<{ providers: string[] }> => {
    return apiFetch('/pricing/providers');
  },

  get: async (modelId: string): Promise<ModelPricing> => {
    return apiFetch(`/pricing/${encodeURIComponent(modelId)}`);
  },

  create: async (data: PricingCreate): Promise<ModelPricing> => {
    return apiFetch('/pricing', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update: async (modelId: string, data: PricingUpdate): Promise<ModelPricing> => {
    return apiFetch(`/pricing/${encodeURIComponent(modelId)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete: async (modelId: string): Promise<{ message: string }> => {
    return apiFetch(`/pricing/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
    });
  },
};
