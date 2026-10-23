// Small inline icon set (stroke icons, 24px grid) so no icon library is needed.
// Decorative by default; pass `title` to give an icon an accessible name.

function Icon({ children, className = "h-5 w-5", title, ...props }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      {...props}
    >
      {title && <title>{title}</title>}
      {children}
    </svg>
  );
}

export const LogoMark = ({ className = "h-8 w-8" }) => (
  <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
    <rect width="32" height="32" rx="8" fill="#4f46e5" />
    <path d="M9 22V10h7a4 4 0 0 1 0 8h-7m7 0 5 4" fill="none" stroke="#fff" strokeWidth="2.6"
          strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const OverviewIcon = (p) => (
  <Icon {...p}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" />
    <rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></Icon>
);
export const FilesIcon = (p) => (
  <Icon {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></Icon>
);
export const ModelIcon = (p) => (
  <Icon {...p}><path d="M4 19V5M4 19h16" /><path d="m7 15 4-4 3 3 5-6" /></Icon>
);
export const BugIcon = (p) => (
  <Icon {...p}><rect x="8" y="7" width="8" height="12" rx="4" /><path d="M9 7a3 3 0 0 1 6 0M4 13h4M16 13h4M5 8l3 2M19 8l-3 2M5 19l3-2M19 19l-3-2M12 11v8" /></Icon>
);
export const PlayIcon = (p) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="m10 8.5 5 3.5-5 3.5z" /></Icon>
);
export const LogoutIcon = (p) => (
  <Icon {...p}><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10" /></Icon>
);
export const MenuIcon = (p) => (
  <Icon {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Icon>
);
export const CloseIcon = (p) => (
  <Icon {...p}><path d="M6 6l12 12M18 6 6 18" /></Icon>
);
export const EyeIcon = (p) => (
  <Icon {...p}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></Icon>
);
export const EyeOffIcon = (p) => (
  <Icon {...p}><path d="M3 3l18 18M10.6 5.1A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.6 6.6A17.4 17.4 0 0 0 2 12s3.5 7 10 7a10.8 10.8 0 0 0 5.4-1.4M9.9 9.9a3 3 0 0 0 4.2 4.2" /></Icon>
);
export const AlertIcon = (p) => (
  <Icon {...p}><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17h.01" /></Icon>
);
export const InfoIcon = (p) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></Icon>
);
export const CheckIcon = (p) => (
  <Icon {...p}><path d="m5 12 4 4 10-10" /></Icon>
);
export const SearchIcon = (p) => (
  <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></Icon>
);
export const ChevronIcon = (p) => (
  <Icon {...p}><path d="m9 6 6 6-6 6" /></Icon>
);
