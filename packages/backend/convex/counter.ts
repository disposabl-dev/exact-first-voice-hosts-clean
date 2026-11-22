import { v } from "convex/values";
import { mutation, query, type QueryCtx } from "./_generated/server";

const counter = (db: QueryCtx["db"]) =>
  db.query("counters").withIndex("by_name", (q) => q.eq("name", "shared")).unique();

// The starter's public demo shares one counter with every installation.
export const get = query({
  args: {},
  returns: v.object({ count: v.number() }),
  handler: async ({ db }) => ({ count: (await counter(db))?.count ?? 0 }),
});

export const increment = mutation({
  args: {},
  returns: v.null(),
  handler: async ({ db }) => {
    const current = await counter(db);
    if (current) {
      await db.patch("counters", current._id, { count: current.count + 1 });
    } else {
      await db.insert("counters", { name: "shared", count: 1 });
    }
    return null;
  },
});
