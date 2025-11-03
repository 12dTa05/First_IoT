'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth-context';
import { apiClient } from '@/lib/api-client';
import { wsClient } from '@/lib/websocket';
import type { DashboardOverview, Device, AccessLog, WebSocketMessage } from '@/types';
import { Activity, Wifi, WifiOff, Clock } from 'lucide-react';

export default function DashboardPage() {
  const router = useRouter();
  const { user, loading: authLoading, logout } = useAuth();
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [recentActivities, setRecentActivities] = useState<AccessLog[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login');
      return;
    }

    if (user) {
      loadDashboardData();
      wsClient.connect();

      const unsubscribe = wsClient.subscribe(handleWebSocketMessage);
      return () => {
        unsubscribe();
        wsClient.disconnect();
      };
    }
  }, [user, authLoading]);

  const loadDashboardData = async () => {
    try {
      const [overviewRes, devicesRes, activitiesRes] = await Promise.all([
        apiClient.getDashboardOverview(),
        apiClient.getDevices(),
        apiClient.getRecentActivities(24),
      ]);

      if (overviewRes.success) setOverview(overviewRes.data!);
      if (devicesRes.success) setDevices(devicesRes.data!);
      if (activitiesRes.success) setRecentActivities(activitiesRes.data!);
    } catch (error) {
      console.error('Failed to load dashboard:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleWebSocketMessage = (message: WebSocketMessage) => {
    if (message.type === 'device_status' && message.device_id) {
      setDevices((prev) =>
        prev.map((dev) =>
          dev.device_id === message.device_id
            ? { ...dev, status: message.data.status, last_seen: message.data.timestamp }
            : dev
        )
      );
    } else if (message.type === 'access_event') {
      loadDashboardData();
    }
  };

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  if (authLoading || loading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-white">Đang tải...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      <header className="bg-slate-800 border-b border-slate-700 px-6 py-4">
        <div className="max-w-7xl mx-auto flex justify-between items-center">
          <h1 className="text-2xl font-bold">IoT Dashboard</h1>
          <div className="flex items-center gap-4">
            <span className="text-slate-400">Xin chào, {user?.username}</span>
            <button
              onClick={handleLogout}
              className="bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded transition"
            >
              Đăng xuất
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
          <div className="bg-slate-800 p-6 rounded-lg border border-slate-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-slate-400 text-sm">Tổng thiết bị</p>
                <p className="text-3xl font-bold mt-1">{overview?.total_devices || 0}</p>
              </div>
              <Activity className="text-blue-500" size={40} />
            </div>
          </div>

          <div className="bg-slate-800 p-6 rounded-lg border border-slate-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-slate-400 text-sm">Đang hoạt động</p>
                <p className="text-3xl font-bold mt-1 text-green-500">
                  {overview?.online_devices || 0}
                </p>
              </div>
              <Wifi className="text-green-500" size={40} />
            </div>
          </div>

          <div className="bg-slate-800 p-6 rounded-lg border border-slate-700">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-slate-400 text-sm">Ngoại tuyến</p>
                <p className="text-3xl font-bold mt-1 text-red-500">
                  {overview?.offline_devices || 0}
                </p>
              </div>
              <WifiOff className="text-red-500" size={40} />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-slate-800 p-6 rounded-lg border border-slate-700">
            <h2 className="text-xl font-semibold mb-4">Thiết bị của tôi</h2>
            <div className="space-y-3">
              {devices.length === 0 ? (
                <p className="text-slate-400">Không có thiết bị nào</p>
              ) : (
                devices.map((device) => (
                  <div
                    key={device.device_id}
                    className="flex items-center justify-between p-4 bg-slate-700/50 rounded hover:bg-slate-700 transition cursor-pointer"
                    onClick={() => router.push(`/devices/${device.device_id}`)}
                  >
                    <div>
                      <p className="font-semibold">{device.device_id}</p>
                      <p className="text-sm text-slate-400">{device.device_type}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {device.status === 'online' ? (
                        <span className="flex items-center gap-1 text-green-500 text-sm">
                          <Wifi size={16} /> Online
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-red-500 text-sm">
                          <WifiOff size={16} /> Offline
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="bg-slate-800 p-6 rounded-lg border border-slate-700">
            <h2 className="text-xl font-semibold mb-4">Hoạt động gần đây</h2>
            <div className="space-y-3">
              {recentActivities.length === 0 ? (
                <p className="text-slate-400">Không có hoạt động nào</p>
              ) : (
                recentActivities.slice(0, 10).map((activity, index) => (
                  <div
                    key={index}
                    className="flex items-center justify-between p-3 bg-slate-700/50 rounded"
                  >
                    <div>
                      <p className="text-sm">
                        <span className="font-semibold">{activity.device_id}</span> - {activity.method}
                      </p>
                      <p className="text-xs text-slate-400 flex items-center gap-1 mt-1">
                        <Clock size={12} />
                        {new Date(activity.time).toLocaleString('vi-VN')}
                      </p>
                    </div>
                    <span
                      className={`px-2 py-1 text-xs rounded ${
                        activity.result === 'granted'
                          ? 'bg-green-500/20 text-green-400'
                          : 'bg-red-500/20 text-red-400'
                      }`}
                    >
                      {activity.result === 'granted' ? 'Cho phép' : 'Từ chối'}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}