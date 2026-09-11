import { Link, useLocation } from 'react-router-dom';
import './Navigation.css';

export default function Navigation() {
  const location = useLocation();

  const isActive = (path: string) => location.pathname === path;

  const navItems = [
    { path: '/', label: 'Home', icon: '🏠' },
    { path: '/reservations', label: 'Reservations', icon: '📅' },
    { path: '/promotions', label: 'Promotions', icon: '🏷️' },
    { path: '/reviews', label: 'Reviews', icon: '💬' },
    { path: '/notifications', label: 'Notifications', icon: '🔔' },
  ];

  return (
    <nav className="navigation">
      <div className="nav-container">
        <div className="nav-brand">
          <span className="nav-icon">⭐</span>
          <span className="nav-title">BARIYON Receptra</span>
        </div>

        <ul className="nav-menu">
          {navItems.map((item) => (
            <li key={item.path}>
              <Link
                to={item.path}
                className={`nav-link ${isActive(item.path) ? 'active' : ''}`}
              >
                <span className="nav-item-icon">{item.icon}</span>
                <span className="nav-item-label">{item.label}</span>
              </Link>
            </li>
          ))}
        </ul>

        <div className="nav-user">
          <span className="user-icon">👤</span>
          <span className="user-name">Admin</span>
        </div>
      </div>
    </nav>
  );
}