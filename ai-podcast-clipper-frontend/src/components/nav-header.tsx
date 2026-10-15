const NavHeader = () => {
  return (
    <header className="bg-background sticky top-0 z-10 flex justify-center border-b">
      <div className="container flex h-16 items-center justify-between px-4 py-2">
        <div className="font-sans text-xl font-medium tracking-tight">
          <span className="text-foreground">podcast</span>
          <span className="font-light text-gray-500">/</span>
          <span className="text-foreground font-light">clipper</span>
        </div>
        <span className="text-muted-foreground text-xs">local</span>
      </div>
    </header>
  );
};

export default NavHeader;
