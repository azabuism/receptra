import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { reservationAPI } from '../services/api';
export default function Dashboard() {
    const [stats, setStats] = useState({
        totalReservations: 0,
        totalCustomers: 0,
        averageRating: 0,
        totalReviews: 0,
    });
    const [reservations, setReservations] = useState([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        const fetchDashboardData = async () => {
            try {
                setLoading(true);
                const [resList] = await Promise.all([
                    reservationAPI.listByShop('shop_123').catch(() => ({ data: [] })),
                ]);
                const reservationsData = resList?.data || [];
                setReservations(reservationsData);
                setStats({
                    totalReservations: reservationsData.length,
                    totalCustomers: Math.floor(Math.random() * 100) + 50,
                    averageRating: parseFloat((Math.random() * 2 + 3).toFixed(1)),
                    totalReviews: Math.floor(Math.random() * 50) + 10,
                });
            }
            catch (error) {
                console.error('Dashboard data fetch error:', error);
            }
            finally {
                setLoading(false);
            }
        };
        fetchDashboardData();
    }, []);
    const StatCard = ({ icon, label, value }) => (_jsxs("div", { className: "stat-card", children: [_jsx("div", { className: "stat-icon", children: icon }), _jsxs("div", { className: "stat-content", children: [_jsx("p", { className: "stat-label", children: label }), _jsx("p", { className: "stat-value", children: value })] })] }));
    if (loading) {
        return _jsx("div", { className: "page", children: _jsx("p", { children: "Loading dashboard..." }) });
    }
    return (_jsxs("div", { className: "page", children: [_jsx("h1", { children: "\u30C0\u30C3\u30B7\u30E5\u30DC\u30FC\u30C9" }), _jsxs("div", { className: "stats-grid", children: [_jsx(StatCard, { icon: "\uD83D\uDCC5", label: "Total Reservations", value: stats.totalReservations }), _jsx(StatCard, { icon: "\uD83D\uDC65", label: "Total Customers", value: stats.totalCustomers }), _jsx(StatCard, { icon: "\u2B50", label: "Avg Rating", value: stats.averageRating }), _jsx(StatCard, { icon: "\uD83D\uDCAC", label: "Total Reviews", value: stats.totalReviews })] }), _jsxs("section", { className: "section", children: [_jsx("h2", { children: "\u6700\u8FD1\u306E\u4E88\u7D04" }), reservations.length > 0 ? (_jsx("div", { className: "table-container", children: _jsxs("table", { className: "data-table", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Date" }), _jsx("th", { children: "Time" }), _jsx("th", { children: "Guests" }), _jsx("th", { children: "Status" })] }) }), _jsx("tbody", { children: reservations.slice(0, 5).map((res) => (_jsxs("tr", { children: [_jsx("td", { children: res.reservation_date }), _jsx("td", { children: res.reservation_time }), _jsx("td", { children: res.guest_count }), _jsx("td", { children: _jsx("span", { className: `status-badge status-${res.status}`, children: res.status }) })] }, res.id))) })] }) })) : (_jsx("p", { children: "No reservations yet" }))] })] }));
}
