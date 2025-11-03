import axios, { AxiosInstance, AxiosError } from 'axios';
import type { ApiResponse, User, Device, DashboardOverview, TelemetryData, AccessLog } from '@/types';

class ApiClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:8090',
      withCredentials: true,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError) => {
        if (error.response?.status === 401) {
          if (typeof window !== 'undefined') {
            window.location.href = '/login';
          }
        }
        return Promise.reject(error);
      }
    );
  }

  async login(username: string, password: string): Promise<ApiResponse<{ user: User }>> {
    const response = await this.client.post('/auth/login', { username, password });
    return response.data;
  }

  async register(username: string, email: string, password: string, full_name?: string): Promise<ApiResponse<{ user: User }>> {
    const response = await this.client.post('/auth/register', {
      username,
      email,
      password,
      full_name,
    });
    return response.data;
  }

  async logout(): Promise<ApiResponse> {
    const response = await this.client.post('/auth/logout');
    return response.data;
  }

  async getCurrentUser(): Promise<User> {
    const response = await this.client.get('/auth/me');
    return response.data;
  }

  async checkSession(): Promise<ApiResponse> {
    const response = await this.client.get('/auth/session');
    return response.data;
  }

  async getDashboardOverview(): Promise<ApiResponse<DashboardOverview>> {
    const response = await this.client.get('/dashboard/overview');
    return response.data;
  }

  async getRecentActivities(hours: number = 24): Promise<ApiResponse<AccessLog[]>> {
    const response = await this.client.get('/dashboard/recent-activities', {
      params: { hours },
    });
    return response.data;
  }

  async getDevices(): Promise<ApiResponse<Device[]>> {
    const response = await this.client.get('/devices');
    return response.data;
  }

  async getDevice(deviceId: string): Promise<ApiResponse<Device>> {
    const response = await this.client.get(`/devices/${deviceId}`);
    return response.data;
  }

  async sendCommand(deviceId: string, commandType: string, parameters?: any): Promise<ApiResponse> {
    const response = await this.client.post(`/devices/${deviceId}/command`, {
      command_type: commandType,
      parameters: parameters || {},
    });
    return response.data;
  }

  async getTelemetry(deviceId: string, hours: number = 24): Promise<ApiResponse<TelemetryData[]>> {
    const response = await this.client.get(`/devices/${deviceId}/telemetry`, {
      params: { hours },
    });
    return response.data;
  }

  async getAccessLogs(deviceId: string, hours: number = 24): Promise<ApiResponse<AccessLog[]>> {
    const response = await this.client.get(`/devices/${deviceId}/access-logs`, {
      params: { hours },
    });
    return response.data;
  }
}

export const apiClient = new ApiClient();