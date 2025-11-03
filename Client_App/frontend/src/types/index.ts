export interface User {
  user_id: string;
  username: string;
  email: string;
  full_name?: string;
  role: string;
}

export interface Device {
  device_id: string;
  gateway_id: string;
  user_id: string;
  device_type: string;
  location?: string;
  communication?: string;
  status: 'online' | 'offline';
  last_seen: string;
  created_at: string;
  updated_at: string;
}

export interface Gateway {
  gateway_id: string;
  user_id: string;
  name: string;
  location?: string;
  status: 'online' | 'offline';
  last_seen: string;
  database_version?: string;
  created_at: string;
  updated_at: string;
  device_count?: number;
}

export interface TelemetryData {
  time: string;
  device_id: string;
  temperature?: number;
  humidity?: number;
  metadata?: any;
}

export interface AccessLog {
  time: string;
  user_id: string;
  device_id: string;
  gateway_id: string;
  method: string;
  identifier?: string;
  result: 'granted' | 'denied';
  reason?: string;
}

export interface DashboardOverview {
  total_devices: number;
  online_devices: number;
  offline_devices: number;
  total_gateways: number;
  online_gateways: number;
  recent_access_count: number;
  recent_alerts?: any[];
}

export interface WebSocketMessage {
  type: 'connection' | 'device_status' | 'access_event' | 'telemetry' | 'alert';
  data?: any;
  device_id?: string;
  user_id?: string;
  message?: string;
}

export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}