export function Card({
  title,
  description,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-card rounded-xl border p-4 sm:p-5">
      <div className="mb-4 space-y-1">
        <h2 className="font-medium">{title}</h2>
        {description && (
          <p className="text-muted-foreground text-sm leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}
