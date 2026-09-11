import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { reservationAPI } from '../services/api';
export default function Reservations() {
    const [reservations, setReservations] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const [loading, setLoading] = useState(true);
    const [formData, setFormData] = useState({
        shop_id: 'shop_123',
        customer_id: 'customer_123',
        reservation_date: '',
        reservation_time: '',
        guest_count: 2,
        special_requests: '',
    });
    useEffect(() => {
        fetchReservations();
    }, []);
    const fetchReservations = async () => {
        try {
            setLoading(true);
            const res = await reservationAPI.listByShop('shop_123');
            setReservations(res.data || []);
        }
        catch (error) {
            console.error('Error fetching reservations:', error);
        }
        finally {
            setLoading(false);
        }
    };
    const handleSubmit = async (e) => {
        e.preventDefault();
        try {
            await reservationAPI.create(formData);
            setFormData({
                shop_id: 'shop_123',
                customer_id: 'customer_123',
                reservation_date: '',
                reservation_time: '',
                guest_count: 2,
                special_requests: '',
            });
            setShowForm(false);
            fetchReservations();
        }
        catch (error) {
            console.error('Error creating reservation:', error);
        }
    };
    const handleDelete = async (id) => {
        try {
            await reservationAPI.delete(id);
            fetchReservations();
        }
        catch (error) {
            console.error('Error deleting reservation:', error);
        }
    };
    if (loading) {
        return _jsx("div", { className: "page", children: _jsx("p", { children: "Loading reservations..." }) });
    }
    return (_jsxs("div", { className: "page", children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { children: "\u4E88\u7D04\u7BA1\u7406" }), _jsx("button", { className: "btn btn-primary", onClick: () => setShowForm(!showForm), children: "\u2795 \u65B0\u898F\u4E88\u7D04" })] }), showForm && (_jsxs("form", { className: "form", onSubmit: handleSubmit, children: [_jsx("input", { type: "date", required: true, value: formData.reservation_date || '', onChange: (e) => setFormData({ ...formData, reservation_date: e.target.value }), placeholder: "Date" }), _jsx("input", { type: "time", required: true, value: formData.reservation_time || '', onChange: (e) => setFormData({ ...formData, reservation_time: e.target.value }), placeholder: "Time" }), _jsx("input", { type: "number", min: "1", required: true, value: formData.guest_count || 2, onChange: (e) => setFormData({ ...formData, guest_count: parseInt(e.target.value) }), placeholder: "Guest count" }), _jsx("textarea", { value: formData.special_requests || '', onChange: (e) => setFormData({ ...formData, special_requests: e.target.value }), placeholder: "Special requests" }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { type: "submit", className: "btn btn-primary", children: "Save" }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: () => setShowForm(false), children: "Cancel" })] })] })), _jsx("section", { className: "section", children: _jsx("div", { className: "table-container", children: _jsxs("table", { className: "data-table", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Date" }), _jsx("th", { children: "Time" }), _jsx("th", { children: "Guests" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Actions" })] }) }), _jsx("tbody", { children: reservations.map((res) => (_jsxs("tr", { children: [_jsx("td", { children: res.reservation_date }), _jsx("td", { children: res.reservation_time }), _jsx("td", { children: res.guest_count }), _jsx("td", { children: _jsx("span", { className: `status-badge status-${res.status}`, children: res.status }) }), _jsx("td", { children: _jsx("button", { className: "btn btn-sm btn-danger", onClick: () => handleDelete(res.id), children: "Delete" }) })] }, res.id))) })] }) }) })] }));
}
