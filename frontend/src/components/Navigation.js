import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Link, useLocation } from 'react-router-dom';
import './Navigation.css';
export default function Navigation() {
    const location = useLocation();
    const isActive = (path) => location.pathname === path;
    const navItems = [
        { path: '/', label: 'Home', icon: '🏠' },
        { path: '/reservations', label: 'Reservations', icon: '📅' },
        { path: '/promotions', label: 'Promotions', icon: '🏷️' },
        { path: '/reviews', label: 'Reviews', icon: '💬' },
        { path: '/notifications', label: 'Notifications', icon: '🔔' },
    ];
    return (_jsx("nav", { className: "navigation", children: _jsxs("div", { className: "nav-container", children: [_jsxs("div", { className: "nav-brand", children: [_jsx("span", { className: "nav-icon", children: "\u2B50" }), _jsx("span", { className: "nav-title", children: "BARIYON Receptra" })] }), _jsx("ul", { className: "nav-menu", children: navItems.map((item) => (_jsx("li", { children: _jsxs(Link, { to: item.path, className: `nav-link ${isActive(item.path) ? 'active' : ''}`, children: [_jsx("span", { className: "nav-item-icon", children: item.icon }), _jsx("span", { className: "nav-item-label", children: item.label })] }) }, item.path))) }), _jsxs("div", { className: "nav-user", children: [_jsx("span", { className: "user-icon", children: "\uD83D\uDC64" }), _jsx("span", { className: "user-name", children: "Admin" })] })] }) }));
}
