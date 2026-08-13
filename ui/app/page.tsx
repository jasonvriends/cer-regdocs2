import { AtlasChat } from "@/components/atlas-chat";
import { cookies } from "next/headers";

export default async function Home() {
  const cookieStore = await cookies();
  const defaultSidebarOpen = cookieStore.get("sidebar_state")?.value !== "false";

  return <AtlasChat defaultSidebarOpen={defaultSidebarOpen} />;
}
