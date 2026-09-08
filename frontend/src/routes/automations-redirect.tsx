import React from "react";

export default function AutomationsRedirect() {
  React.useEffect(() => {
    const { pathname, search, hash } = window.location;
    const suffix = pathname.replace(/^\/automations/, "");
    window.location.replace(`/canvas/automations${suffix}${search}${hash}`);
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-base">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
    </div>
  );
}
