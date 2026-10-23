import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
});

export const fetchMetrics = async (storeId: string) => {
  const res = await api.get(`/stores/${storeId}/metrics`);
  return res.data;
};

export const fetchFunnel = async (storeId: string) => {
  const res = await api.get(`/stores/${storeId}/funnel`);
  return res.data;
};

export const fetchAnomalies = async (storeId: string) => {
  const res = await api.get(`/stores/${storeId}/anomalies`);
  return res.data;
};

export const fetchHeatmap = async (storeId: string, cameraId: string) => {
  const res = await api.get(`/stores/${storeId}/heatmap`, { params: { camera_id: cameraId } });
  return res.data;
};

export const getEventStreamUrl = () => {
  // Mock event stream for now, or connect to a real WebSocket if implemented.
  // The API currently doesn't have a live event stream endpoint via SSE/WS.
  return `${API_BASE_URL}/events/stream`;
};

export default api;
