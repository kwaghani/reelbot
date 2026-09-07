import Ionicons from "@expo/vector-icons/Ionicons";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  Inter_400Regular,
  Inter_600SemiBold,
  Inter_700Bold
} from "@expo-google-fonts/inter";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentProps } from "react";
import type { NativeScrollEvent, NativeSyntheticEvent } from "react-native";
import {
  ActivityIndicator,
  Alert,
  Animated,
  AppState,
  Easing,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Linking,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import {
  addItemsToGroup,
  askQuestion,
  createGroup,
  deleteItem,
  getGroupItems,
  getGroups,
  getItems,
  isAuthorizationError,
  joinGroup,
  setApiContext,
  getJob,
  type Group,
  type QueryAnswer,
  type SavedItem
} from "./api";
import { getApiConfigIssue } from "./config";
import { getDeviceId, resetDeviceSession } from "./identity";
import {
  readLastShareReceipt,
  type ActiveGroup,
  type ShareReceipt,
  writeShareSettings
} from "./sharedGroup";
import { theme } from "./theme";

const DISPLAY_NAME_KEY = "reelbot.displayName";
const ACTIVE_GROUP_KEY = "reelbot.activeGroupId";
const RECENT_SHARE_WINDOW_MS = 10 * 60 * 1000;

type TabKey = "saved" | "folders" | "groups" | "ask";
type IconName = ComponentProps<typeof Ionicons>["name"];

const FOLDER_ICONS: Record<string, IconName> = {
  restaurants: "restaurant",
  "cafes & desserts": "cafe",
  "bars & nightlife": "wine",
  recipes: "flame",
  workouts: "barbell",
  sports: "football",
  travel: "airplane",
  "things to do": "compass",
  outdoors: "leaf",
  "relationships & dating": "heart",
  "comedy & memes": "happy",
  humor: "happy",
  "shopping & products": "bag-handle",
  shopping: "bag-handle",
  "fashion & style": "shirt",
  fashion: "shirt",
  "beauty & skincare": "sparkles",
  beauty: "sparkles",
  "home & diy": "home",
  "home & decor": "home",
  "tech & gadgets": "hardware-chip",
  tech: "hardware-chip",
  "cars & bikes": "car-sport",
  cars: "car-sport",
  "movies & tv": "film",
  entertainment: "film",
  music: "musical-notes",
  gaming: "game-controller",
  "pets & animals": "paw",
  "books & reading": "book",
  "learning & how-to": "school",
  learning: "school",
  "health & wellness": "fitness",
  "motivation & mindset": "trending-up",
  "money & career": "cash",
  finance: "cash",
  "creative ideas": "bulb",
  ideas: "bulb",
  other: "folder"
};

function folderIcon(name: string): IconName {
  return FOLDER_ICONS[name.trim().toLowerCase()] ?? "folder";
}

export default function App() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_600SemiBold,
    Inter_700Bold
  });
  const fontsReady = fontsLoaded || Boolean(fontError);

  const [booting, setBooting] = useState(true);
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [pendingName, setPendingName] = useState("");
  const [activeTab, setActiveTab] = useState<TabKey>("saved");
  const [sharedStorageError, setSharedStorageError] = useState<string | null>(null);
  const [shareReceipt, setShareReceipt] = useState<ShareReceipt | null>(null);
  const [deviceId, setDeviceId] = useState("");
  const [setupError, setSetupError] = useState<string | null>(null);
  const [setupAttempt, setSetupAttempt] = useState(0);
  const [groups, setGroups] = useState<Group[]>([]);
  const [activeGroup, setActiveGroup] = useState<ActiveGroup | null>(null);
  const selectedGroupRef = useRef<string | null>(null);
  const groupSelectionRef = useRef(Promise.resolve());

  const apiConfigIssue = getApiConfigIssue();
  const apiReady = !apiConfigIssue;

  const refreshShareReceipt = useCallback(async () => {
    const receipt = await readLastShareReceipt();
    if (receipt?.jobId && Date.now() - receipt.createdAt <= RECENT_SHARE_WINDOW_MS) {
      const job = await getJob(receipt.jobId);
      if (!["queued", "processing"].includes(job.status)) { setShareReceipt(null); return; }
      setShareReceipt(receipt);
      return;
    }
    setShareReceipt(null);
  }, []);



  const selectGroup = useCallback(
    (group: ActiveGroup) => {
      selectedGroupRef.current = group.id;
      // Write destinations in selection order so the extension cannot retain
      // an older group's settings after a slower write completes.
      groupSelectionRef.current = groupSelectionRef.current.then(async () => {
        try {
          await AsyncStorage.setItem(ACTIVE_GROUP_KEY, group.id);
          if (displayName) await writeShareSettings(displayName, { deviceId, activeGroup: group });
          if (selectedGroupRef.current !== group.id) return;
          setActiveGroup(group);
          setApiContext({ groupId: group.id });
          setSharedStorageError(null);
        } catch {
          setSharedStorageError("Could not update the sharing destination. Retry the library selection before sharing.");
        }
      });
    },
    [deviceId, displayName]
  );

  const loadGroups = useCallback(async () => {
    if (!apiReady || !displayName || !deviceId) {
      return;
    }
    try {
      const fetched = await getGroups(displayName);
      setSetupError(null);
      setGroups(fetched);
      const storedId = await AsyncStorage.getItem(ACTIVE_GROUP_KEY);
      const chosen = fetched.find((group) => group.id === (selectedGroupRef.current || storedId)) ?? fetched[0] ?? null;
      if (chosen && (chosen.id !== activeGroup?.id || sharedStorageError)) {
        selectGroup({ id: chosen.id, name: chosen.name });
      }
    } catch (error) {
      if (isAuthorizationError(error)) { setGroups([]); setActiveGroup(null); setApiContext({ groupId: "" }); }
      setSetupError(error instanceof Error ? error.message : "Could not load your libraries. Please retry.");
    }
  }, [activeGroup?.id, apiReady, deviceId, displayName, selectGroup, sharedStorageError]);

  useEffect(() => {
    loadGroups().catch(() => undefined);
  }, [loadGroups]);

  const handleGroupChosen = useCallback(
    (group: Group) => {
      setGroups((current) => {
        const exists = current.some((candidate) => candidate.id === group.id);
        return exists
          ? current.map((candidate) => (candidate.id === group.id ? group : candidate))
          : [...current, group];
      });
      selectGroup({ id: group.id, name: group.name });
    },
    [selectGroup]
  );

  useEffect(() => {
    async function loadIdentity() {
      setSetupError(null);
      const savedName = await AsyncStorage.getItem(DISPLAY_NAME_KEY);
      if (apiReady) {
        const id = await getDeviceId();
        setDeviceId(id);
      }
      if (savedName) {
        setDisplayName(savedName);
        setPendingName(savedName);
        if (apiReady) {
          try {
            await refreshShareReceipt();
          } catch (error) {
            if (!isAuthorizationError(error)) setSharedStorageError(error instanceof Error ? error.message : "Could not check the latest share. Please retry.");
          }
        }
      }
      setBooting(false);
    }

    loadIdentity().catch((error) => { setSetupError(error instanceof Error ? error.message : "Could not restore this device."); setBooting(false); });
  }, [apiReady, refreshShareReceipt, setupAttempt]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        refreshShareReceipt().catch(() => undefined);
      }
    });

    return () => subscription.remove();
  }, [refreshShareReceipt]);

  const saveDisplayName = useCallback(async () => {
    const cleanName = pendingName.replace(/\s+/g, " ").trim();
    if (!cleanName) {
      return;
    }

    try { await AsyncStorage.setItem(DISPLAY_NAME_KEY, cleanName); }
    catch { setSetupError("Could not save your name. Please try again."); return; }
    setDisplayName(cleanName);
    if (apiReady) {
      try {
        if (activeGroup) await writeShareSettings(cleanName, { deviceId, activeGroup });
        await refreshShareReceipt();
        setSharedStorageError(null);
      } catch (error) {
        setSharedStorageError(error instanceof Error ? error.message : "Could not update sharing settings. Please retry.");
      }
    }
  }, [activeGroup, apiReady, deviceId, pendingName, refreshShareReceipt]);

  const resetConnection = useCallback(() => {
    Alert.alert("Reset device connection?", "Saved reels stay in their groups. This creates a new device session; rejoin private groups with their invite codes.", [
      { text: "Cancel", style: "cancel" },
      { text: "Reset connection", onPress: () => {
        (async () => {
          await resetDeviceSession();
          await AsyncStorage.removeItem(ACTIVE_GROUP_KEY);
          selectedGroupRef.current = null;
          setActiveGroup(null); setGroups([]); setDeviceId("");
          setSetupAttempt((n) => n + 1);
        })().catch(() => setSetupError("Could not reset the device connection. Please try again."));
      } }
    ]);
  }, []);

  if (booting || !fontsReady) {
    return (
      <SafeAreaView style={styles.shell}>
        <StatusBar style="dark" />
        <View style={styles.centered}>
          <ActivityIndicator color={theme.colors.accent} />
        </View>
      </SafeAreaView>
    );
  }

  if (!displayName) {
    return (
      <SafeAreaView style={styles.shell}>
        <StatusBar style="dark" />
        <KeyboardAvoidingView
          behavior={Platform.select({ ios: "padding", default: undefined })}
          style={styles.nameGate}
        >
          <View style={styles.namePanel}>
            <View style={styles.nameIcon}>
              <Image source={require("../assets/icon.png")} style={styles.brandImage} />
            </View>
            <Text style={styles.nameEyebrow}>YOUR SHARED REEL INDEX</Text>
            <Text style={styles.nameTitle}>First, what should we call you?</Text>
            <Text style={styles.nameDescription}>
              Your name helps friends see who saved what. You can keep it simple.
            </Text>
            {setupError ? <Text accessibilityRole="alert" style={styles.inlineError}>{setupError}</Text> : null}
            {setupError ? <Pressable accessibilityRole="button" style={styles.connectionButton} onPress={() => setSetupAttempt((n) => n + 1)}><Text style={styles.retryButtonText}>Retry setup</Text></Pressable> : null}
            {setupError ? <Pressable accessibilityRole="button" style={styles.connectionButton} onPress={resetConnection}><Text style={styles.retryButtonText}>Reset connection</Text></Pressable> : null}
            {apiConfigIssue ? <Banner text={apiConfigIssue} /> : null}
            <Text style={styles.nameFieldLabel}>NAME</Text>
            <TextInput
              accessibilityLabel="Your name"
              maxLength={120}
              autoCapitalize="words"
              autoCorrect={false}
              onChangeText={setPendingName}
              onSubmitEditing={saveDisplayName}
              placeholder="Your name"
              placeholderTextColor={theme.colors.textSecondary}
              returnKeyType="done"
              style={styles.nameInput}
              value={pendingName}
            />
            <Pressable
              accessibilityRole="button"
              disabled={!pendingName.trim() || !deviceId}
              onPress={() => saveDisplayName().catch(() => setSetupError("Could not finish setup. Please retry."))}
              style={({ pressed }) => [
                styles.primaryButton,
                pressed && styles.primaryButtonPressed,
                !pendingName.trim() && styles.disabledButton
              ]}
            >
              <Text style={styles.primaryButtonText}>Continue</Text>
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.shell}>
      <StatusBar style="dark" />
      <KeyboardAvoidingView
        behavior={activeTab === "ask" ? Platform.select({ ios: "padding", default: undefined }) : undefined}
        style={styles.appBody}
      >
        <View style={styles.header}>
          <View style={styles.brandRow}>
            <View style={styles.brandMark}>
              <Image source={require("../assets/icon.png")} style={styles.brandImage} />
            </View>
            <Text style={styles.appTitle}>ReelBot</Text>
          </View>
          <View style={styles.userChip}>
            <View style={styles.userAvatar}>
              <Text style={styles.userAvatarText}>{displayName.charAt(0).toUpperCase()}</Text>
            </View>
            <Text numberOfLines={1} style={[styles.userLabel, { maxWidth: 120 }]}>{displayName}</Text>
          </View>
        </View>

        {apiConfigIssue && <Banner text={apiConfigIssue} />}
        {sharedStorageError && <Banner text={sharedStorageError} />}
        {setupError ? <View><Banner text={setupError} /><Pressable accessibilityRole="button" style={styles.connectionButton} onPress={() => { setSetupAttempt((n) => n + 1); loadGroups().catch(() => undefined); }}><Text style={styles.retryButtonText}>Retry connection</Text></Pressable><Pressable accessibilityRole="button" style={styles.connectionButton} onPress={resetConnection}><Text style={styles.retryButtonText}>Reset connection</Text></Pressable></View> : null}
        {activeGroup ? <Text numberOfLines={1} style={styles.libraryScope}>Library: {activeGroup.name}{groups.find((g) => g.id === activeGroup.id)?.join_code ? "" : " · Shared with all testers"}</Text> : null}

        <View style={styles.content}>
          {activeTab === "saved" ? (
            <SavedScreen
              key={`SavedScreen-${activeGroup?.id}`}
              apiReady={apiReady && Boolean(deviceId) && Boolean(activeGroup)}
              groupKey={activeGroup?.id ?? ""}
              shareReceipt={shareReceipt}
              refreshShareReceipt={refreshShareReceipt}
            />
          ) : activeTab === "folders" ? (
            <FoldersScreen key={`folders-${activeGroup?.id}`} apiReady={apiReady && Boolean(deviceId) && Boolean(activeGroup)} groupKey={activeGroup?.id ?? ""} />
          ) : activeTab === "groups" ? (
            <GroupsScreen
              activeGroupId={activeGroup?.id ?? null}
              apiReady={apiReady && Boolean(deviceId) && Boolean(activeGroup)}
              groups={groups}
              onChoose={handleGroupChosen}
              onRefreshGroups={loadGroups}
              userName={displayName}
            />
          ) : (
            <AskScreen
              key={`AskScreen-${activeGroup?.id}`}
              apiReady={apiReady && Boolean(deviceId) && Boolean(activeGroup)}
              displayName={displayName}
              groupKey={activeGroup?.id ?? ""}
              groupName={activeGroup?.name ?? "Shared Saves"}
              onManageGroups={() => setActiveTab("groups")}
            />
          )}
        </View>

        <View style={styles.tabBar}>
          <TabButton
            active={activeTab === "saved"}
            iconName={activeTab === "saved" ? "bookmark" : "bookmark-outline"}
            label="Saved"
            onPress={() => setActiveTab("saved")}
          />
          <TabButton
            active={activeTab === "folders"}
            iconName={activeTab === "folders" ? "folder-open" : "folder-outline"}
            label="Folders"
            onPress={() => setActiveTab("folders")}
          />
          <TabButton
            active={activeTab === "groups"}
            iconName={activeTab === "groups" ? "people" : "people-outline"}
            label="Groups"
            onPress={() => setActiveTab("groups")}
          />
          <TabButton
            active={activeTab === "ask"}
            iconName={activeTab === "ask" ? "chatbubble-ellipses" : "chatbubble-ellipses-outline"}
            label="Ask"
            onPress={() => setActiveTab("ask")}
          />
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Banner({ text }: { text: string }) {
  return (
    <View style={styles.banner}>
      <Text style={styles.bannerText}>{text}</Text>
    </View>
  );
}

function TabButton({
  active,
  iconName,
  label,
  onPress
}: {
  active: boolean;
  iconName: IconName;
  label: string;
  onPress: () => void;
}) {
  const color = active ? theme.colors.accent : theme.colors.textSecondary;

  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={label}
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.tabButton,
        active && styles.tabButtonActive,
        pressed && styles.tabButtonPressed
      ]}
    >
      <Ionicons color={color} name={iconName} size={21} />
      <Text style={[styles.tabLabel, active && styles.activeTabLabel]}>{label}</Text>
    </Pressable>
  );
}

function shareGroupInvite(group: Group) {
  if (!group.join_code) {
    return;
  }
  Share.share({
    message: `Join my ReelBot group "${group.name}" with code ${group.join_code}.`
  }).catch(() => undefined);
}

function GroupsScreen({
  apiReady,
  groups,
  activeGroupId,
  userName,
  onChoose,
  onRefreshGroups
}: {
  apiReady: boolean;
  groups: Group[];
  activeGroupId: string | null;
  userName: string;
  onChoose: (group: Group) => void;
  onRefreshGroups: () => Promise<void>;
}) {
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [items, setItems] = useState<SavedItem[]>([]);
  const [loadingItems, setLoadingItems] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [action, setAction] = useState<"create" | "join" | null>(null);
  const [newName, setNewName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [busy, setBusy] = useState<"create" | "join" | null>(null);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const groupRequest = useRef(0);
  const selectedGroup = groups.find((group) => group.id === selectedGroupId) ?? null;

  useEffect(() => {
    if (!apiReady || addOpen) return;
    const refresh = () => {
      if (AppState.currentState !== "active") return;
      onRefreshGroups().catch(() => undefined);
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    const subscription = AppState.addEventListener("change", (state) => { if (state === "active") refresh(); });
    return () => { clearInterval(timer); subscription.remove(); };
  }, [apiReady, addOpen, onRefreshGroups]);

  const loadSelectedItems = useCallback(async (showSpinner = true) => {
    const request = ++groupRequest.current;
    if (!apiReady || !selectedGroupId) {
      setItems([]);
      return;
    }
    if (showSpinner) {
      setLoadingItems(true);
    }
    setError(null);
    try {
      const fetched = await getGroupItems(selectedGroupId);
      if (request !== groupRequest.current) return;
      setItems(fetched.filter((item) => item.status === "saved"));
    } catch (err) {
      if (request === groupRequest.current && isAuthorizationError(err)) setItems([]);
      if (request === groupRequest.current) setError(err instanceof Error ? err.message : "Could not load this shared list.");
    } finally {
      if (request === groupRequest.current) { setLoadingItems(false); setRefreshing(false); }
    }
  }, [apiReady, selectedGroupId]);

  useEffect(() => {
    setItems([]);
    loadSelectedItems().catch(() => undefined);
    return () => { groupRequest.current += 1; };
  }, [loadSelectedItems]);

  useEffect(() => {
    if (!apiReady || !selectedGroupId || addOpen) return;
    const timer = setInterval(() => {
      if (AppState.currentState === "active") loadSelectedItems(false).catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [apiReady, selectedGroupId, addOpen, loadSelectedItems]);

  const openGroup = useCallback((group: Group) => {
    onChoose(group);
    setSelectedGroupId(group.id);
    setError(null);
  }, [onChoose]);

  const submitCreate = useCallback(async () => {
    const cleanName = newName.replace(/\s+/g, " ").trim();
    if (!cleanName || busyRef.current) {
      return;
    }
    busyRef.current = true;
    setBusy("create");
    setError(null);
    try {
      const group = await createGroup(cleanName, userName);
      await onRefreshGroups();
      onChoose(group);
      setNewName("");
      setAction(null);
      setSelectedGroupId(group.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create that group.");
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }, [busy, newName, onChoose, onRefreshGroups, userName]);

  const submitJoin = useCallback(async () => {
    const cleanCode = joinCode.trim();
    if (!cleanCode || busyRef.current) {
      return;
    }
    busyRef.current = true;
    setBusy("join");
    setError(null);
    try {
      const group = await joinGroup(cleanCode, userName);
      await onRefreshGroups();
      onChoose(group);
      setJoinCode("");
      setAction(null);
      setSelectedGroupId(group.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not join with that code.");
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }, [busy, joinCode, onChoose, onRefreshGroups, userName]);

  if (selectedGroup) {
    return (
      <View style={styles.groupsScreen}>
        <Pressable
          accessibilityRole="button"
          onPress={() => setSelectedGroupId(null)}
          style={styles.breadcrumb}
        >
          <Ionicons color={theme.colors.accent} name="chevron-back" size={20} />
          <Text style={styles.groupsBackText}>All groups</Text>
        </Pressable>
        <ScrollView
          contentContainerStyle={styles.groupDetailContent}
          refreshControl={
            <RefreshControl
              onRefresh={() => {
                setRefreshing(true);
                loadSelectedItems(false).catch(() => undefined);
              }}
              refreshing={refreshing}
              tintColor={theme.colors.accent}
            />
          }
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.groupDetailHero}>
            <View style={styles.groupDetailIcon}>
              <Ionicons color={theme.colors.accent} name="people" size={24} />
            </View>
            <View style={styles.groupDetailCopy}>
              <Text style={styles.groupDetailEyebrow}>SHARED LIST</Text>
              <Text style={styles.groupDetailTitle}>{selectedGroup.name}</Text>
              <Text style={styles.groupDetailMeta}>
                {selectedGroup.member_count} {selectedGroup.member_count === 1 ? "member" : "members"} · {items.length} {items.length === 1 ? "reel" : "reels"}
              </Text>
            </View>
          </View>
          <Text style={styles.groupDetailDescription}>
            Everyone in this group can add reels. Use this list to collect places, activities, ideas, or anything you want to do together.
          </Text>
          <View style={styles.groupDetailActions}>
            <Pressable
              accessibilityRole="button"
              onPress={() => setAddOpen(true)}
              style={({ pressed }) => [styles.groupPrimaryAction, pressed && styles.primaryButtonPressed]}
            >
              <Ionicons color={theme.colors.card} name="add" size={18} />
              <Text style={styles.groupPrimaryActionText}>Add reels</Text>
            </Pressable>
            {selectedGroup.join_code ? (
              <Pressable
                accessibilityRole="button"
                onPress={() => shareGroupInvite(selectedGroup)}
                style={({ pressed }) => [styles.groupSecondaryAction, pressed && styles.sourceRowPressed]}
              >
                <Ionicons color={theme.colors.accent} name="person-add-outline" size={17} />
                <Text style={styles.groupSecondaryActionText}>Invite</Text>
              </Pressable>
            ) : null}
          </View>
          {selectedGroup.join_code ? (
            <View style={styles.groupInviteCodeRow}>
              <Text style={styles.groupInviteCodeLabel}>INVITE CODE</Text>
              <Text style={styles.groupInviteCodeValue}>{selectedGroup.join_code}</Text>
            </View>
          ) : null}
          <View style={styles.groupListHeadingRow}>
            <Text style={styles.groupListHeading}>Shared reels</Text>
            <Text style={styles.groupListCount}>{items.length}</Text>
          </View>
          {error ? <Text style={styles.inlineError}>{error}</Text> : null}
          {loadingItems ? (
            <View style={styles.groupLoadingArea}>
              <ActivityIndicator color={theme.colors.accent} />
            </View>
          ) : items.length ? (
            <View style={styles.groupItemsList}>
              {items.map((item) => (
                <ItemCard item={item} key={item.id ?? item.source_url ?? itemTitle(item)} />
              ))}
            </View>
          ) : (
            <View style={styles.groupListEmpty}>
              <Ionicons color={theme.colors.textSecondary} name="sparkles-outline" size={25} />
              <Text style={styles.groupListEmptyTitle}>Build this list together</Text>
              <Text style={styles.groupListEmptyText}>
                Add several reels from one of your existing folders to get started.
              </Text>
            </View>
          )}
        </ScrollView>
        <AddReelsModal
          groups={groups}
          onAdded={() => {
            setAddOpen(false);
            loadSelectedItems().catch(() => undefined);
            onRefreshGroups().catch(() => undefined);
          }}
          onClose={() => setAddOpen(false)}
          targetGroup={selectedGroup}
          userName={userName}
          visible={addOpen}
        />
      </View>
    );
  }

  return (
    <View style={styles.groupsScreen}>
      <View style={styles.groupsHeader}>
        <View>
          <Text style={styles.screenTitle}>Groups</Text>
          <Text style={styles.groupsSubtitle}>Shared lists for plans you make together.</Text>
        </View>
        <View style={styles.groupsHeaderActions}>
          <Pressable
            accessibilityLabel="Create a group"
            accessibilityRole="button"
            onPress={() => setAction(action === "create" ? null : "create")}
            style={({ pressed }) => [styles.groupsHeaderButton, pressed && styles.sourceRowPressed]}
          >
            <Ionicons color={theme.colors.accent} name="add" size={19} />
          </Pressable>
          <Pressable
            accessibilityLabel="Join a group"
            accessibilityRole="button"
            onPress={() => setAction(action === "join" ? null : "join")}
            style={({ pressed }) => [styles.groupsHeaderButton, pressed && styles.sourceRowPressed]}
          >
            <Ionicons color={theme.colors.accent} name="enter-outline" size={18} />
          </Pressable>
        </View>
      </View>

      {action ? (
        <View style={styles.groupsInlineForm}>
          <View style={styles.groupsInlineFormCopy}>
            <Text style={styles.groupsInlineFormTitle}>
              {action === "create" ? "Create a shared list" : "Join your friends"}
            </Text>
            <Pressable accessibilityRole="button" accessibilityLabel="Close group form" hitSlop={12} onPress={() => setAction(null)}>
              <Ionicons color={theme.colors.textSecondary} name="close" size={18} />
            </Pressable>
          </View>
          <View style={styles.groupsInlineInputRow}>
            <TextInput
              accessibilityLabel={action === "join" ? "Invite code" : "Group name"}
              maxLength={action === "join" ? 12 : 60}
              autoCapitalize={action === "join" ? "characters" : "words"}
              autoCorrect={action === "create"}
              onChangeText={action === "join" ? setJoinCode : setNewName}
              onSubmitEditing={action === "join" ? submitJoin : submitCreate}
              placeholder={action === "join" ? "Invite code" : "Group name"}
              placeholderTextColor={theme.colors.textSecondary}
              returnKeyType="done"
              style={styles.groupsInlineInput}
              value={action === "join" ? joinCode : newName}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={action === "join" ? "Join group" : "Create group"}
              disabled={busy !== null || !(action === "join" ? joinCode.trim() : newName.trim())}
              onPress={action === "join" ? submitJoin : submitCreate}
              style={({ pressed }) => [
                styles.groupsInlineSubmit,
                pressed && styles.primaryButtonPressed,
                (busy !== null || !(action === "join" ? joinCode.trim() : newName.trim())) && styles.disabledButton
              ]}
            >
              {busy === action ? (
                <ActivityIndicator color={theme.colors.card} size="small" />
              ) : (
                <Ionicons color={theme.colors.card} name="arrow-forward" size={18} />
              )}
            </Pressable>
          </View>
          {error ? <Text style={styles.groupActionError}>{error}</Text> : null}
        </View>
      ) : null}

      <ScrollView contentContainerStyle={styles.groupsOverviewList} showsVerticalScrollIndicator={false}>
        {groups.map((group) => {
          const tint = theme.folderTint(group.name);
          const isActive = group.id === activeGroupId;
          return (
            <Pressable
              accessibilityLabel={`Open ${group.name}`}
              accessibilityRole="button"
              key={group.id}
              onPress={() => openGroup(group)}
              style={({ pressed }) => [
                styles.groupsOverviewCard,
                isActive && styles.groupsOverviewCardActive,
                pressed && styles.itemCardPressed
              ]}
            >
              <View style={[styles.groupsOverviewIcon, { backgroundColor: tint.background }]}>
                <Ionicons color={tint.foreground} name="people" size={21} />
              </View>
              <View style={styles.groupsOverviewCopy}>
                <View style={styles.groupsOverviewTitleRow}>
                  <Text numberOfLines={1} style={styles.groupsOverviewTitle}>{group.name}</Text>
                  {isActive ? <View style={styles.groupsActiveDot} /> : null}
                </View>
                <Text style={styles.groupsOverviewMeta}>
                  {group.item_count} {group.item_count === 1 ? "reel" : "reels"} · {group.member_count} {group.member_count === 1 ? "member" : "members"}
                </Text>
                <Text style={styles.groupsOverviewHint}>Open shared list</Text>
              </View>
              <Ionicons color={theme.colors.textSecondary} name="chevron-forward" size={17} />
            </Pressable>
          );
        })}
        {groups.length === 0 ? (
          <EmptyState headline="No groups yet" text="Create a group or join one with an invite code." />
        ) : null}
      </ScrollView>
    </View>
  );
}

function AddReelsModal({
  visible,
  targetGroup,
  groups,
  userName,
  onClose,
  onAdded
}: {
  visible: boolean;
  targetGroup: Group;
  groups: Group[];
  userName: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const sourceGroups = useMemo(
    () => groups.filter((group) => group.id !== targetGroup.id),
    [groups, targetGroup.id]
  );
  const [sourceGroupId, setSourceGroupId] = useState("");
  const [items, setItems] = useState<SavedItem[]>([]);
  const [folder, setFolder] = useState("All");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const initialSourceGroupId = sourceGroups[0]?.id ?? "";
  useEffect(() => {
    if (!visible) {
      return;
    }
    setSourceGroupId(initialSourceGroupId);
    setFolder("All");
    setSelectedIds([]);
    setError(null);
  }, [initialSourceGroupId, targetGroup.id, visible]);

  useEffect(() => {
    if (!visible || !sourceGroupId) {
      setItems([]);
      return;
    }
    let active = true;
    setItems([]);
    setLoading(true);
    setError(null);
    getGroupItems(sourceGroupId)
      .then((fetched) => { if (active) setItems(fetched.filter((item) => item.status === "saved" && item.id)); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Could not load source reels."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sourceGroupId, visible]);

  const folders = useMemo(
    () => ["All", ...Array.from(new Set(items.map((item) => item.list_name?.trim() || "Other"))).sort()],
    [items]
  );
  const visibleItems = folder === "All"
    ? items
    : items.filter((item) => (item.list_name?.trim() || "Other") === folder);
  const visibleIds = visibleItems.flatMap((item) => item.id ? [item.id] : []);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));

  const toggleItem = useCallback((id: string) => {
    setSelectedIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }, []);

  const toggleVisible = useCallback(() => {
    setSelectedIds((current) => {
      if (visibleIds.every((id) => current.includes(id))) {
        return current.filter((id) => !visibleIds.includes(id));
      }
      return Array.from(new Set([...current, ...visibleIds]));
    });
  }, [visibleIds]);

  const submit = useCallback(async () => {
    if (!sourceGroupId || !selectedIds.length || selectedIds.length > 100 || saving || loading) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await addItemsToGroup(targetGroup.id, sourceGroupId, selectedIds, userName);
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add those reels.");
    } finally {
      setSaving(false);
    }
  }, [onAdded, saving, selectedIds, sourceGroupId, targetGroup.id, userName]);

  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <SafeAreaView style={styles.addReelsSheet}>
        <View style={styles.addReelsHeader}>
          <View style={styles.addReelsHeaderCopy}>
            <Text style={styles.groupDetailEyebrow}>ADD TO {targetGroup.name.toUpperCase()}</Text>
            <Text style={styles.addReelsTitle}>Choose several reels</Text>
          </View>
          <Pressable accessibilityLabel="Close reel picker" accessibilityRole="button" disabled={saving} onPress={onClose} style={styles.groupSheetClose}>
            <Ionicons color={theme.colors.textPrimary} name="close" size={20} />
          </Pressable>
        </View>
        {sourceGroups.length ? (
          <>
            <Text style={styles.addReelsSectionLabel}>FROM LIBRARY</Text>
            <ScrollView
              contentContainerStyle={styles.addReelsChips}
              style={styles.addReelsChipScroller}
              horizontal
              showsHorizontalScrollIndicator={false}
            >
              {sourceGroups.map((group) => (
                <Pressable
                  accessibilityRole="button"
                  key={group.id}
                  onPress={() => {
                    setSourceGroupId(group.id);
                    setSelectedIds([]);
                    setFolder("All");
                  }}
                  style={[styles.filterChip, sourceGroupId === group.id && styles.filterChipActive]}
                >
                  <Text style={[styles.filterChipText, sourceGroupId === group.id && styles.filterChipTextActive]}>{group.name}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <Text style={styles.addReelsSectionLabel}>FOLDER</Text>
            <ScrollView
              contentContainerStyle={styles.addReelsChips}
              style={styles.addReelsChipScroller}
              horizontal
              showsHorizontalScrollIndicator={false}
            >
              {folders.map((name) => (
                <Pressable
                  accessibilityRole="button"
                  key={name}
                  onPress={() => setFolder(name)}
                  style={[styles.filterChip, folder === name && styles.filterChipActive]}
                >
                  <Text style={[styles.filterChipText, folder === name && styles.filterChipTextActive]}>{name}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <View style={styles.addReelsSelectionHeader}>
              <Text style={styles.addReelsSelectionCount}>{selectedIds.length} selected (up to 100)</Text>
              <Pressable accessibilityRole="button" onPress={toggleVisible}>
                <Text style={styles.addReelsSelectAll}>{allVisibleSelected ? "Clear folder" : "Select folder"}</Text>
              </Pressable>
            </View>
            {error ? <Text style={styles.inlineError}>{error}</Text> : null}
            {loading ? (
              <View style={styles.centered}><ActivityIndicator color={theme.colors.accent} /></View>
            ) : (
              <FlatList
                contentContainerStyle={styles.addReelsList}
                data={visibleItems}
                keyExtractor={(item) => item.id ?? item.source_url ?? itemTitle(item)}
                renderItem={({ item }) => {
                  const id = item.id as string;
                  const selected = selectedIds.includes(id);
                  return (
                    <Pressable
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked: selected }}
                      onPress={() => toggleItem(id)}
                      style={({ pressed }) => [styles.addReelRow, selected && styles.addReelRowSelected, pressed && styles.sourceRowPressed]}
                    >
                      <View style={[styles.addReelCheck, selected && styles.addReelCheckSelected]}>
                        {selected ? <Ionicons color={theme.colors.card} name="checkmark" size={15} /> : null}
                      </View>
                      <View style={styles.addReelCopy}>
                        <Text numberOfLines={1} style={styles.addReelTitle}>{itemTitle(item)}</Text>
                        <Text numberOfLines={1} style={styles.addReelMeta}>
                          {[item.list_name, item.subfolder, item.location_text].filter(Boolean).join(" › ")}
                        </Text>
                      </View>
                    </Pressable>
                  );
                }}
                ListEmptyComponent={<Text style={styles.answerMuted}>No reels in this folder.</Text>}
              />
            )}
            <View style={styles.addReelsFooter}>
              <Pressable
                accessibilityRole="button"
                disabled={!selectedIds.length || selectedIds.length > 100 || saving || loading}
                onPress={submit}
                style={({ pressed }) => [styles.addReelsSubmit, pressed && styles.primaryButtonPressed, (!selectedIds.length || selectedIds.length > 100 || saving || loading) && styles.disabledButton]}
              >
                {saving ? <ActivityIndicator color={theme.colors.card} /> : <Text style={styles.addReelsSubmitText}>Add {selectedIds.length || ""} {selectedIds.length === 1 ? "reel" : "reels"}</Text>}
              </Pressable>
            </View>
          </>
        ) : (
          <View style={styles.groupListEmpty}>
            <Text style={styles.groupListEmptyTitle}>No source library available</Text>
            <Text style={styles.groupListEmptyText}>Save reels to another library first, then bring them into this group.</Text>
          </View>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function SavedScreen({
  apiReady,
  groupKey,
  shareReceipt,
  refreshShareReceipt
}: {
  apiReady: boolean;
  groupKey: string;
  shareReceipt: ShareReceipt | null;
  refreshShareReceipt: () => Promise<void>;
}) {
  const [items, setItems] = useState<SavedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadingRef = useRef(false);
  const loadItems = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (!apiReady || loadingRef.current) {
        setLoading(false);
        return;
      }
      loadingRef.current = true;

      if (mode === "refresh") {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);

      try {
        const [nextItems] = await Promise.all([getItems(), refreshShareReceipt().catch(() => undefined)]);
        setItems(nextItems);
      } catch (err) {
        if (isAuthorizationError(err)) setItems([]);
        setError(err instanceof Error ? err.message : "Could not load saved places.");
      } finally {
        loadingRef.current = false;
        setLoading(false);
        setRefreshing(false);
      }
    },
    // groupKey retriggers the load when the active group changes.
    [apiReady, refreshShareReceipt, groupKey]
  );

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => { if (state === "active") loadItems("refresh"); });
    const timer = setInterval(() => { if (AppState.currentState === "active") loadItems("refresh"); }, 5000);
    return () => { subscription.remove(); clearInterval(timer); };
  }, [loadItems]);

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={theme.colors.accent} />
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <View style={styles.screenHeader}>
        <Text style={styles.screenTitle}>Saved</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Refresh saved reels"
          onPress={() => loadItems("refresh")}
          style={({ pressed }) => [styles.iconButton, pressed && styles.iconButtonPressed]}
        >
          <Ionicons color={theme.colors.accent} name="refresh" size={20} />
        </Pressable>
      </View>

      {error && <Text style={styles.inlineError}>{error}</Text>}

      <FlatList
        contentContainerStyle={items.length ? styles.listContent : styles.emptyList}
        data={items}
        keyExtractor={(item, index) =>
          `${item.source_url || item.place_name || item.location_text || "place"}-${item.status}-${index}`
        }
        refreshControl={
          <RefreshControl
            colors={[theme.colors.accent]}
            onRefresh={() => loadItems("refresh")}
            progressBackgroundColor={theme.colors.card}
            refreshing={refreshing}
            tintColor={theme.colors.accent}
          />
        }
        renderItem={({ item }) => <ItemCard item={item} />}
        ListEmptyComponent={error || !apiReady ? null : <EmptyState />}
        ListHeaderComponent={!error && shareReceipt?.groupId === groupKey && !items.some((item) => item.job_id === shareReceipt.jobId || item.source_url === shareReceipt.url) ? <PendingShare receipt={shareReceipt} /> : null}
      />
    </View>
  );
}

function PendingShare({ receipt }: { receipt: ShareReceipt }) {
  return (
    <View style={styles.pendingCard}>
      <ActivityIndicator color={theme.colors.accent} size="small" />
      <View style={styles.pendingTextWrap}>
        <Text style={styles.pendingTitle}>Queued for processing</Text>
        <Text numberOfLines={2} style={styles.pendingText}>
          Reel sent to ReelBot. It will appear here after the worker finishes.
        </Text>
        <Text numberOfLines={1} style={styles.pendingUrl}>
          {receipt.url}
        </Text>
      </View>
    </View>
  );
}

function EmptyState({
  headline = "No reels saved yet",
  text = "Share a reel from Instagram, TikTok, or YouTube Shorts to get started."
}: {
  headline?: string;
  text?: string;
}) {
  return (
    <View style={styles.emptyState}>
      <Image resizeMode="contain" source={require("../assets/empty-state.png")} style={styles.emptyImage} />
      <Text style={styles.emptyHeadline}>{headline}</Text>
      <Text style={styles.emptyText}>{text}</Text>
    </View>
  );
}

function openSource(url: string) {
  if (!/^https?:\/\//i.test(url)) { Alert.alert("Cannot open link", "This reel has no valid source link."); return; }
  Linking.openURL(url).catch(() => Alert.alert("Could not open the source", "Check your connection and try again. The original platform may require you to sign in."));
}

function ItemCard({ item }: { item: SavedItem }) {
  const itemStatus = item.status ?? "saved";
  const isSaved = itemStatus === "saved";
  const metaLine = [item.category, item.location_text].filter(Boolean).join(" • ");
  const title = isSaved
    ? item.place_name || "Saved place"
    : itemStatus === "error"
      ? "Could not save reel"
      : "Processing reel";
  const detail = isSaved ? metaLine : item.message || "Reel sent to ReelBot. It will appear here after processing.";
  const folderName = item.list_name?.trim() || "Other";
  const tint = theme.folderTint(folderName);
  const folderLabel = item.subfolder?.trim() ? `${folderName} › ${item.subfolder.trim()}` : folderName;

  return (
    <Pressable
      accessibilityRole={item.source_url ? "link" : "none"}
      disabled={!item.source_url}
      onPress={() => item.source_url && openSource(item.source_url)}
      style={({ pressed }) => [styles.itemCard, pressed && styles.itemCardPressed]}
    >
      <View style={styles.cardTopRow}>
        <View
          style={[
            styles.itemTile,
            isSaved
              ? { backgroundColor: tint.background }
              : { backgroundColor: itemStatus === "error" ? theme.colors.dangerSoft : theme.colors.accentSoft }
          ]}
        >
          {isSaved ? (
            <Ionicons color={tint.foreground} name={folderIcon(folderName)} size={20} />
          ) : itemStatus === "error" ? (
            <Ionicons color={theme.colors.danger} name="alert-circle-outline" size={20} />
          ) : (
            <ActivityIndicator color={theme.colors.accent} size="small" />
          )}
        </View>
        <View style={styles.cardTitleWrap}>
          <Text numberOfLines={2} style={styles.placeName}>
            {title}
          </Text>
          {detail ? (
            <Text numberOfLines={isSaved ? 2 : undefined} style={styles.itemMeta}>
              {detail}
            </Text>
          ) : null}
        </View>
        {isSaved && item.save_count > 1 ? (
          <View style={styles.saveCount}>
            <Ionicons color={theme.colors.accent} name="bookmark" size={13} />
            <Text style={styles.saveCountText}>{item.save_count}</Text>
          </View>
        ) : null}
      </View>

      {isSaved || item.source_url ? (
        <View style={styles.cardFooterRow}>
          <View style={[styles.chip, isSaved && { backgroundColor: tint.background }]}>
            <Text numberOfLines={1} style={[styles.chipText, isSaved && { color: tint.foreground }]}>
              {isSaved ? folderLabel : item.source_url}
            </Text>
          </View>
          {isSaved && item.source_url ? (
            <View style={styles.watchHint}>
              <Ionicons color={theme.colors.textSecondary} name="play-circle-outline" size={14} />
              <Text style={styles.watchHintText}>Watch</Text>
            </View>
          ) : null}
        </View>
      ) : null}
    </Pressable>
  );
}

function itemTitle(item: SavedItem): string {
  return item.place_name?.trim() || "Saved reel";
}

function FolderItemRow({ item, onDelete }: { item: SavedItem; onDelete: (item: SavedItem) => void }) {
  const meta = [item.category, item.location_text].filter(Boolean).join(" · ");

  const confirmDelete = useCallback(() => {
    Alert.alert(
      "Delete this reel?",
      `"${itemTitle(item)}" and its saved data will be removed for the whole group.`,
      [
        { style: "cancel", text: "Cancel" },
        { onPress: () => onDelete(item), style: "destructive", text: "Delete" }
      ]
    );
  }, [item, onDelete]);

  return (
    <View style={styles.folderItemRow}>
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`Open ${itemTitle(item)}`}
      disabled={!item.source_url}
      onPress={() => item.source_url && openSource(item.source_url)}
      style={({ pressed }) => [styles.folderItemSource, pressed && styles.sourceRowPressed]}
    >
      <View style={styles.playTile}>
        <Ionicons color={theme.colors.accent} name="play" size={16} />
      </View>
      <View style={styles.folderItemBody}>
        <Text numberOfLines={1} style={styles.folderItemTitle}>
          {itemTitle(item)}
        </Text>
        {meta ? (
          <Text numberOfLines={1} style={styles.folderItemMeta}>
            {meta}
          </Text>
        ) : null}
      </View>
    </Pressable>
      {item.id ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Delete ${itemTitle(item)} for the group`}
          hitSlop={8}
          onPress={(event) => { event.stopPropagation(); confirmDelete(); }}
          style={({ pressed }) => [styles.deleteButton, pressed && styles.sourceRowPressed]}
        >
          <Ionicons color={theme.colors.danger} name="trash-outline" size={17} />
        </Pressable>
      ) : (
        <Ionicons color={theme.colors.textSecondary} name="open-outline" size={16} />
      )}
    </View>
  );
}

type FolderSection = "all" | "activities" | "location";

type LibraryNode = {
  name: string;
  items: SavedItem[];
  directItems: SavedItem[];
  children: LibraryNode[];
};

function buildLibraryHierarchy(
  items: SavedItem[],
  pathForItem: (item: SavedItem) => string[]
): LibraryNode[] {
  const roots: LibraryNode[] = [];
  for (const item of items) {
    const path = pathForItem(item).map((part) => part.trim()).filter(Boolean);
    if (!path.length) {
      continue;
    }
    let level = roots;
    path.forEach((name, index) => {
      let node = level.find((candidate) => candidate.name.toLocaleLowerCase() === name.toLocaleLowerCase());
      if (!node) {
        node = { name, items: [], directItems: [], children: [] };
        level.push(node);
      }
      node.items.push(item);
      if (index === path.length - 1) {
        node.directItems.push(item);
      }
      level = node.children;
    });
  }

  const sortLevel = (nodes: LibraryNode[]): LibraryNode[] => nodes
    .map((node) => ({ ...node, children: sortLevel(node.children) }))
    .sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name));
  return sortLevel(roots);
}

function activityPath(item: SavedItem): string[] {
  const broad = item.list_name?.trim() || "Other";
  const specific = item.subfolder?.trim();
  return specific && specific.toLocaleLowerCase() !== broad.toLocaleLowerCase()
    ? [broad, specific]
    : [broad];
}

function locationPath(item: SavedItem): string[] {
  const parts = (item.location_text || "")
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part && !/^\d{4,6}$/.test(part));
  if (parts.length <= 1) {
    return parts;
  }
  return [...parts].reverse();
}

function findLibraryNode(nodes: LibraryNode[], path: string[]): LibraryNode | null {
  let level = nodes;
  let node: LibraryNode | null = null;
  for (const name of path) {
    node = level.find((candidate) => candidate.name === name) ?? null;
    if (!node) {
      return null;
    }
    level = node.children;
  }
  return node;
}

function FoldersScreen({ apiReady, groupKey }: { apiReady: boolean; groupKey: string }) {
  const [items, setItems] = useState<SavedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<FolderSection>("all");
  const [path, setPath] = useState<string[]>([]);
  const requestSequence = useRef(0);
  const deleting = useRef(new Set<string>());

  useEffect(() => {
    setPath([]);
  }, [groupKey]);

  const load = useCallback(async () => {
    const request = ++requestSequence.current;
    if (!apiReady) {
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const fetched = await getItems();
      if (request !== requestSequence.current) return;
      setItems(fetched.filter((item) => item.status === "saved"));
    } catch (err) {
      if (isAuthorizationError(err)) setItems([]);
      setError(err instanceof Error ? err.message : "Could not load folders.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
    // groupKey retriggers the load when the active group changes.
  }, [apiReady, groupKey]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load().catch(() => undefined);
  }, [load]);

  const removeItem = useCallback(
    async (item: SavedItem) => {
      if (!item.id || deleting.current.has(item.id)) {
        return;
      }
      deleting.current.add(item.id);
      try {
        await deleteItem(item.id, groupKey);
        requestSequence.current += 1;
        setItems((current) => current.filter((candidate) => candidate.id !== item.id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not delete that reel.");
      } finally { deleting.current.delete(item.id); }
    },
    [groupKey]
  );

  const activityTree = useMemo(() => buildLibraryHierarchy(items, activityPath), [items]);
  const locationTree = useMemo(() => buildLibraryHierarchy(items, locationPath), [items]);
  const currentTree = section === "activities" ? activityTree : locationTree;
  const currentNode = section === "all" ? null : findLibraryNode(currentTree, path);
  const visibleNodes = currentNode ? currentNode.children : currentTree;
  const visibleItems = section === "all" ? items : currentNode?.directItems ?? [];

  const changeSection = useCallback((next: FolderSection) => {
    setSection(next);
    setPath([]);
  }, []);

  return (
    <View style={styles.folderScreen}>
      <Text style={styles.screenTitle}>Folders</Text>
      <Text style={styles.folderSubtitle}>Browse every reel by activity or by place.</Text>
      <View style={styles.folderSectionTabs}>
        {([
          ["all", "ALL", "albums-outline"],
          ["activities", "Activities", "compass-outline"],
          ["location", "Location", "location-outline"]
        ] as Array<[FolderSection, string, IconName]>).map(([key, label, icon]) => (
          <Pressable
            accessibilityRole="tab"
            accessibilityState={{ selected: section === key }}
            accessibilityLabel={label}
            key={key}
            onPress={() => changeSection(key)}
            style={[styles.folderSectionTab, section === key && styles.folderSectionTabActive]}
          >
            <Ionicons
              color={section === key ? theme.colors.card : theme.colors.textSecondary}
              name={icon}
              size={15}
            />
            <Text style={[styles.folderSectionTabText, section === key && styles.folderSectionTabTextActive]}>
              {label}
            </Text>
          </Pressable>
        ))}
      </View>
      {path.length ? (
        <View style={styles.folderPathRow}>
          <Pressable
            accessibilityLabel="Go up one folder level"
            accessibilityRole="button"
            onPress={() => setPath((current) => current.slice(0, -1))}
            style={styles.folderPathBack}
          >
            <Ionicons color={theme.colors.accent} name="chevron-back" size={18} />
          </Pressable>
          <Text numberOfLines={1} style={styles.folderPathText}>
            {path.join(" › ")}
          </Text>
        </View>
      ) : null}

      {error && <Text style={styles.inlineError}>{error}</Text>}

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator color={theme.colors.accent} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={styles.folderList}
          refreshControl={
            <RefreshControl onRefresh={onRefresh} refreshing={refreshing} tintColor={theme.colors.accent} />
          }
          showsVerticalScrollIndicator={false}
        >
          {section !== "all" && !path.length && visibleNodes.length ? (
            <View style={styles.folderGrid}>
              {visibleNodes.map((node) => {
                const tint = theme.folderTint(node.name);
                return (
                  <Pressable
                    accessibilityRole="button"
                    key={node.name}
                    onPress={() => setPath([node.name])}
                    style={({ pressed }) => [styles.folderTileCard, pressed && styles.itemCardPressed]}
                  >
                    <View style={[styles.folderTileIcon, { backgroundColor: tint.background }]}>
                      <Ionicons
                        color={tint.foreground}
                        name={section === "location" ? "location" : folderIcon(node.name)}
                        size={24}
                      />
                    </View>
                    <Text numberOfLines={1} style={styles.folderTileName}>{node.name}</Text>
                    <Text style={styles.folderTileCount}>
                      {node.items.length} {node.items.length === 1 ? "reel" : "reels"}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          ) : null}
          {section !== "all" && path.length ? visibleNodes.map((node) => {
            const tint = theme.folderTint(path[0] || node.name);
            return (
              <Pressable
                accessibilityRole="button"
                key={`${path.join("/")}/${node.name}`}
                onPress={() => setPath((current) => [...current, node.name])}
                style={({ pressed }) => [styles.folderRow, pressed && styles.sourceRowPressed]}
              >
                <View style={[styles.folderRowIcon, { backgroundColor: tint.background }]}>
                  <Ionicons
                    color={tint.foreground}
                    name={section === "location" ? "navigate-outline" : "folder-open-outline"}
                    size={18}
                  />
                </View>
                <View style={styles.folderNodeCopy}>
                  <Text numberOfLines={1} style={styles.folderName}>{node.name}</Text>
                  <Text style={styles.folderNodeHint}>Inside {path.at(-1)}</Text>
                </View>
                <Text style={styles.folderCount}>{node.items.length}</Text>
                <Ionicons color={theme.colors.textSecondary} name="chevron-forward" size={16} />
              </Pressable>
            );
          }) : null}
          {visibleItems.map((item) => (
            <FolderItemRow
              item={item}
              key={`${section}-${path.join("/")}-${item.id ?? item.source_url ?? itemTitle(item)}`}
              onDelete={removeItem}
            />
          ))}
          {!items.length && !error && apiReady ? (
            <EmptyState headline="No folders yet" text="Share a reel and it will be filed here automatically." />
          ) : section !== "all" && !visibleNodes.length && !visibleItems.length ? (
            <Text style={styles.answerMuted}>
              {section === "location" ? "No location information is available yet." : "Nothing in this folder yet."}
            </Text>
          ) : null}
        </ScrollView>
      )}
    </View>
  );
}

const ASK_SUGGESTIONS: Array<{
  eyebrow: string;
  icon: IconName;
  prompt: string;
  tint: "accent" | "aqua" | "lavender" | "gold";
}> = [
  {
    eyebrow: "Plan a night",
    icon: "restaurant-outline",
    prompt: "What should we eat this weekend?",
    tint: "accent"
  },
  {
    eyebrow: "Get moving",
    icon: "barbell-outline",
    prompt: "Show me the workouts",
    tint: "aqua"
  },
  {
    eyebrow: "Break the ice",
    icon: "heart-outline",
    prompt: "Give me a good pickup line",
    tint: "lavender"
  },
  {
    eyebrow: "Explore saves",
    icon: "folder-open-outline",
    prompt: "What's in my folders?",
    tint: "gold"
  }
];

// Start a clean presentation-era thread instead of resurfacing older,
// unstructured lowercase replies after the update.
const CHAT_STORAGE_KEY = "reelbot.chat.v2";
const MAX_STORED_MESSAGES = 40;

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  sources?: QueryAnswer["sources"];
  createdAt?: number;
};

function suggestionTint(tint: (typeof ASK_SUGGESTIONS)[number]["tint"]): {
  background: string;
  foreground: string;
} {
  if (tint === "aqua") {
    return { background: theme.colors.chatAqua, foreground: theme.colors.chatAquaInk };
  }
  if (tint === "lavender") {
    return { background: theme.colors.chatLavender, foreground: theme.colors.chatLavenderInk };
  }
  if (tint === "gold") {
    return { background: theme.colors.chatGold, foreground: theme.colors.chatGoldInk };
  }
  return { background: theme.colors.accentSoft, foreground: theme.colors.accent };
}

function messageTime(timestamp?: number): string | null {
  if (!timestamp) {
    return null;
  }
  return new Date(timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function normalizeChatPunctuation(text: string): string {
  const properNouns: Array<[RegExp, string]> = [
    // Keep "La Brea" intact while normalizing the lowercase city abbreviation.
    [/\bla\b/g, "LA"],
    [/\bnyc\b/gi, "NYC"],
    [/\bsf\b/gi, "SF"],
    [/\blos angeles\b/gi, "Los Angeles"],
    [/\bnew york city\b/gi, "New York City"],
    [/\bnew york\b/gi, "New York"],
    [/\bpoint dume\b/gi, "Point Dume"],
    [/\bhollywood sign\b/gi, "Hollywood Sign"],
    [/\bmalibu\b/gi, "Malibu"],
    [/\bcalifornia\b/gi, "California"],
    [/\bmanhattan\b/gi, "Manhattan"],
    [/\bbrooklyn\b/gi, "Brooklyn"]
  ];
  let normalizedText = text;
  properNouns.forEach(([pattern, replacement]) => {
    normalizedText = normalizedText.replace(pattern, replacement);
  });

  return normalizedText
    .split("\n")
    .map((line) => {
      let normalized = line;
      const firstLetter = normalized.search(/[A-Za-z]/);
      if (firstLetter >= 0) {
        normalized = `${normalized.slice(0, firstLetter)}${normalized[firstLetter].toUpperCase()}${normalized.slice(firstLetter + 1)}`;
      }
      return normalized.replace(
        /([.!?]["'”’)]*\s+)([a-z])/g,
        (_match, boundary: string, letter: string) => `${boundary}${letter.toUpperCase()}`
      );
    })
    .join("\n");
}

function AssistantMessageText({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <View style={styles.messageTextStack}>
      {lines.map((line, index) => {
        const clean = line.trim();
        if (!clean) {
          return <View key={`space-${index}`} style={styles.messageTextSpacer} />;
        }

        const bullet = clean.match(/^(?:•|-|\d+[.)])\s+(.+)$/);
        if (bullet) {
          const content = bullet[1];
          const namedDetail = content.match(/^(.+?)\s+[—–-]\s+(.+)$/);
          return (
            <View key={`bullet-${index}`} style={styles.messageBulletRow}>
              <View style={styles.messageBulletDot} />
              <Text style={styles.messageBulletText}>
                {namedDetail ? (
                  <>
                    <Text style={styles.messageBulletTitle}>{namedDetail[1]}</Text>
                    {` — ${namedDetail[2]}`}
                  </>
                ) : (
                  content
                )}
              </Text>
            </View>
          );
        }

        const nextLine = lines[index + 1]?.trim() ?? "";
        const followedByList = /^(?:•|-|\d+[.)])\s+/.test(nextLine);
        const looksLikeHeading = followedByList && clean.length <= 42 && !/[.!?]$/.test(clean);
        return (
          <Text
            key={`line-${index}`}
            style={looksLikeHeading ? styles.messageSectionHeading : styles.answerText}
          >
            {clean}
          </Text>
        );
      })}
    </View>
  );
}

function AskScreen({
  apiReady,
  displayName,
  groupKey,
  groupName,
  onManageGroups
}: {
  apiReady: boolean;
  displayName: string;
  groupKey: string;
  groupName: string;
  onManageGroups: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const mounted = useRef(true);
  const sending = useRef(false);
  useEffect(() => () => { mounted.current = false; }, []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const stickToLatestRef = useRef(true);
  const pendingAutoScrollRef = useRef(false);
  const didInitialScrollRef = useRef(false);
  const storageKey = `${CHAT_STORAGE_KEY}.${groupKey || "default"}`;

  useEffect(() => {
    setHydrated(false);
    setMessages([]);
    stickToLatestRef.current = true;
    pendingAutoScrollRef.current = false;
    didInitialScrollRef.current = false;
    AsyncStorage.getItem(storageKey)
      .then((stored) => {
        if (stored) {
          pendingAutoScrollRef.current = true;
          const parsed: unknown = JSON.parse(stored);
          if (Array.isArray(parsed)) setMessages(parsed.filter((message) => message && typeof message.text === "string" && ["user", "assistant"].includes(message.role)).slice(-MAX_STORED_MESSAGES));
        }
      })
      .catch(() => undefined)
      .finally(() => setHydrated(true));
  }, [storageKey]);

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    AsyncStorage.setItem(storageKey, JSON.stringify(messages.slice(-MAX_STORED_MESSAGES))).catch(
      () => setError("Could not store this conversation on the device.")
    );
  }, [hydrated, messages, storageKey]);

  const canSend = apiReady && hydrated && question.trim().length > 0 && !loading;

  const submitQuestion = useCallback(async (prompt: string, appendUser = true) => {
    const cleanQuestion = prompt.trim();
    if (!cleanQuestion || sending.current || !hydrated || !apiReady) {
      return;
    }

    sending.current = true;
    const historyMessages = appendUser
      ? messages
      : messages.at(-1)?.role === "user" && messages.at(-1)?.text === cleanQuestion
        ? messages.slice(0, -1)
        : messages;
    const history = historyMessages
      .slice(-12)
      .map((message) => ({ role: message.role, text: message.text }));
    const sentAt = Date.now();
    if (appendUser) {
      stickToLatestRef.current = true;
      pendingAutoScrollRef.current = true;
      setMessages((current) => [
        ...current,
        { id: `u-${sentAt}`, role: "user", text: cleanQuestion, createdAt: sentAt }
      ]);
    }
    setQuestion("");
    setLoading(true);
    setError(null);
    setFailedQuestion(null);
    try {
      const reply = await askQuestion(cleanQuestion, displayName, history);
      if (!mounted.current) return;
      const normalizedAnswer = reply.answer;
      pendingAutoScrollRef.current = stickToLatestRef.current;
      setMessages((current) => [
        ...current,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: normalizedAnswer,
          sources: reply.sources,
          createdAt: Date.now()
        }
      ]);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof Error ? err.message : "Could not answer that.");
      setFailedQuestion(cleanQuestion);
    } finally {
      sending.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [apiReady, displayName, hydrated, loading, messages]);

  const sendQuestion = useCallback(() => {
    submitQuestion(question).catch(() => undefined);
  }, [question, submitQuestion]);

  const handleChatScroll = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const distanceFromLatest = contentSize.height - layoutMeasurement.height - contentOffset.y;
    stickToLatestRef.current = distanceFromLatest < 72;
  }, []);

  const handleChatContentSizeChange = useCallback(() => {
    if (!pendingAutoScrollRef.current) {
      return;
    }
    pendingAutoScrollRef.current = false;
    const animated = didInitialScrollRef.current;
    requestAnimationFrame(() => {
      scrollRef.current?.scrollToEnd({ animated });
      didInitialScrollRef.current = true;
    });
  }, []);

  const clearChat = useCallback(() => {
    Alert.alert("Start a new chat?", "This clears the current conversation.", [
      { style: "cancel", text: "Cancel" },
      {
        onPress: () => {
          setMessages([]);
          setError(null);
          setFailedQuestion(null);
          pendingAutoScrollRef.current = false;
          scrollRef.current?.scrollTo({ animated: false, y: 0 });
          AsyncStorage.removeItem(storageKey).catch(() => setError("Could not clear the saved conversation. Please retry."));
        },
        style: "destructive",
        text: "New chat"
      }
    ]);
  }, [storageKey]);

  return (
      <View style={styles.askScreen}>
        <View style={styles.chatHeader}>
          <View style={styles.chatIdentity}>
            <View style={styles.botAvatar}>
              <Image source={require("../assets/icon.png")} style={styles.brandImage} />
            </View>
            <View style={styles.chatIdentityCopy}>
              <View style={styles.chatTitleRow}>
                <Text style={styles.chatTitle}>ReelBot</Text>
                <View style={styles.contextBadge}>
                  <Ionicons color={theme.colors.chatAquaInk} name="bookmark" size={10} />
                  <Text style={styles.contextBadgeText}>Saved reels</Text>
                </View>
              </View>
              <Pressable
                accessibilityLabel={`Switch library from ${groupName}`}
                accessibilityRole="button"
                onPress={onManageGroups}
                style={({ pressed }) => [styles.chatLibraryButton, pressed && styles.sourceRowPressed]}
              >
                <Ionicons color={theme.colors.textSecondary} name="people-outline" size={12} />
                <Text numberOfLines={1} style={styles.chatLibraryText}>
                  {groupName}
                </Text>
                <Ionicons color={theme.colors.textSecondary} name="chevron-down" size={11} />
              </Pressable>
            </View>
          </View>
          {messages.length > 0 ? (
            <Pressable
              accessibilityLabel="Start a new chat"
              accessibilityRole="button"
              disabled={loading || !hydrated}
              onPress={clearChat}
              style={({ pressed }) => [styles.newChatButton, pressed && styles.iconButtonPressed]}
            >
              <Ionicons color={theme.colors.chatInk} name="create-outline" size={18} />
            </Pressable>
          ) : null}
        </View>

        <ScrollView
          contentContainerStyle={styles.chatScroll}
          decelerationRate="normal"
          keyboardDismissMode="interactive"
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={handleChatContentSizeChange}
          onScroll={handleChatScroll}
          onScrollBeginDrag={() => {
            pendingAutoScrollRef.current = false;
          }}
          ref={scrollRef}
          scrollEventThrottle={16}
          showsVerticalScrollIndicator={false}
          style={styles.chatArea}
        >
          {messages.length === 0 && !loading ? (
            <View style={styles.askIntro}>
              <View style={styles.chatHero}>
                <View style={styles.heroBotMark}>
                  <Image source={require("../assets/icon.png")} style={styles.brandImage} />
                </View>
                <Text style={styles.chatHeroEyebrow}>ASK YOUR LIBRARY</Text>
                <Text style={styles.chatHeroTitle}>
                  What are we looking for, {displayName.split(" ")[0]}?
                </Text>
                <Text style={styles.chatHeroText}>
                  ReelBot checks your shared saves first, then fills in the gaps when it can.
                </Text>
                <View style={styles.heroCapabilities}>
                  <View style={styles.heroCapability}>
                    <Ionicons color={theme.colors.accent} name="bookmark-outline" size={13} />
                    <Text style={styles.heroCapabilityText}>Your saves</Text>
                  </View>
                  <View style={styles.heroCapability}>
                    <Ionicons color={theme.colors.accent} name="globe-outline" size={13} />
                    <Text style={styles.heroCapabilityText}>General knowledge</Text>
                  </View>
                </View>
              </View>

              <View style={styles.suggestionHeader}>
                <Text style={styles.suggestionHeading}>Try asking</Text>
                <Text style={styles.suggestionHint}>Choose one to send</Text>
              </View>
              <View style={styles.suggestionGrid}>
                {ASK_SUGGESTIONS.map((suggestion) => (
                  <Pressable
                    accessibilityRole="button"
                    disabled={loading || !hydrated || !apiReady}
                    key={suggestion.prompt}
                    onPress={() => submitQuestion(suggestion.prompt).catch(() => undefined)}
                    style={({ pressed }) => [
                      styles.suggestionCard,
                      pressed && styles.suggestionCardPressed
                    ]}
                  >
                    <View
                      style={[
                        styles.suggestionIcon,
                        { backgroundColor: suggestionTint(suggestion.tint).background }
                      ]}
                    >
                      <Ionicons
                        color={suggestionTint(suggestion.tint).foreground}
                        name={suggestion.icon}
                        size={18}
                      />
                    </View>
                    <Text style={styles.suggestionEyebrow}>{suggestion.eyebrow}</Text>
                    <Text numberOfLines={2} style={styles.suggestionPrompt}>
                      {suggestion.prompt}
                    </Text>
                    <Ionicons
                      color={theme.colors.textSecondary}
                      name="arrow-forward"
                      size={15}
                      style={styles.suggestionArrow}
                    />
                  </Pressable>
                ))}
              </View>
            </View>
          ) : (
            <>
              {messages.map((message) =>
                message.role === "user" ? (
                  <View key={message.id} style={styles.userMessageGroup}>
                    <View style={styles.messageMetaRight}>
                      {messageTime(message.createdAt) ? (
                        <Text style={styles.messageTime}>{messageTime(message.createdAt)}</Text>
                      ) : null}
                      <Text style={styles.messageAuthor}>You</Text>
                    </View>
                    <View style={styles.questionBubble}>
                      <Text style={styles.questionBubbleText}>{message.text}</Text>
                    </View>
                  </View>
                ) : (
                  <View key={message.id} style={styles.assistantMessageGroup}>
                    <View style={styles.messageAvatarSmall}>
                      <Image source={require("../assets/icon.png")} style={styles.brandImage} />
                    </View>
                    <View style={styles.assistantMessageBody}>
                      <View style={styles.messageMetaLeft}>
                        <Text style={styles.messageAuthor}>ReelBot</Text>
                        {messageTime(message.createdAt) ? (
                          <Text style={styles.messageTime}>{messageTime(message.createdAt)}</Text>
                        ) : null}
                      </View>
                      <View style={styles.answerBubble}>
                        <AssistantMessageText text={message.text} />
                        {message.sources && message.sources.length > 0 ? (
                          <View style={styles.sourcesBlock}>
                            <View style={styles.sourcesHeadingRow}>
                              <Ionicons color={theme.colors.accent} name="bookmark" size={13} />
                              <Text style={styles.sourcesLabel}>From your saved reels</Text>
                            </View>
                            {message.sources.map((source, index) => (
                              <Pressable
                                accessibilityRole="link"
                                key={`${message.id}-${source.url}`}
                                onPress={() => openSource(source.url)}
                                style={({ pressed }) => [
                                  styles.sourceCard,
                                  pressed && styles.sourceRowPressed
                                ]}
                              >
                                <View style={styles.sourceIndex}>
                                  <Text style={styles.sourceIndexText}>{index + 1}</Text>
                                </View>
                                <View style={styles.sourceCopy}>
                                  <Text numberOfLines={1} style={styles.sourceTitle}>
                                    {source.title}
                                  </Text>
                                  <Text style={styles.sourceMeta}>Open saved reel</Text>
                                </View>
                                <View style={styles.sourceOpenIcon}>
                                  <Ionicons color={theme.colors.accent} name="play" size={13} />
                                </View>
                              </Pressable>
                            ))}
                          </View>
                        ) : null}
                      </View>
                    </View>
                  </View>
                )
              )}
              {loading && (
                <View style={styles.assistantMessageGroup}>
                  <View style={styles.messageAvatarSmall}>
                    <Image source={require("../assets/icon.png")} style={styles.brandImage} />
                  </View>
                  <View style={styles.assistantMessageBody}>
                    <Text style={styles.messageAuthor}>ReelBot</Text>
                    <View style={styles.thinkingBubble}>
                      <TypingDots />
                    </View>
                  </View>
                </View>
              )}
            </>
          )}
        </ScrollView>

        {error ? (
          <View style={styles.chatError}>
            <View style={styles.chatErrorIcon}>
              <Ionicons color={theme.colors.danger} name="cloud-offline-outline" size={17} />
            </View>
            <Text numberOfLines={2} style={styles.chatErrorText}>
              {error}
            </Text>
            {failedQuestion ? (
              <Pressable
                accessibilityRole="button"
                disabled={loading}
                onPress={() => submitQuestion(failedQuestion, false).catch(() => undefined)}
                style={({ pressed }) => [styles.retryButton, pressed && styles.sourceRowPressed]}
              >
                <Text style={styles.retryButtonText}>Retry</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        <View style={styles.composerShell}>
          <View style={styles.composerTopline}>
            <View style={styles.composerStatusDot} />
            <Text style={styles.composerHint}>Searching {groupName} first</Text>
          </View>
          <View style={styles.askComposer}>
            <TextInput
              accessibilityLabel="Message ReelBot"
              maxLength={1000}
              multiline
              onChangeText={(value) => {
                setQuestion(value);
                if (error) {
                  setError(null);
                  setFailedQuestion(null);
                }
              }}
              placeholder="Message ReelBot…"
              placeholderTextColor={theme.colors.textSecondary}
              returnKeyType="default"
              style={styles.askInput}
              value={question}
            />
            <Pressable
              accessibilityLabel="Send message"
              accessibilityRole="button"
              disabled={!canSend}
              onPress={sendQuestion}
              style={({ pressed }) => [
                styles.sendButton,
                pressed && styles.sendButtonPressed,
                !canSend && styles.sendButtonDisabled
              ]}
            >
              {loading ? (
                <ActivityIndicator color={theme.colors.inkText} size="small" />
              ) : (
                <Ionicons color={theme.colors.inkText} name="arrow-up" size={19} />
              )}
            </Pressable>
          </View>
        </View>
      </View>
  );
}

function TypingDots() {
  const values = useRef([
    new Animated.Value(0),
    new Animated.Value(0),
    new Animated.Value(0)
  ]).current;

  useEffect(() => {
    const animation = Animated.loop(
      Animated.sequence([
        Animated.stagger(
          115,
          values.map((value) => Animated.sequence([
            Animated.timing(value, {
              duration: 190,
              easing: Easing.out(Easing.cubic),
              toValue: 1,
              useNativeDriver: true
            }),
            Animated.timing(value, {
              duration: 230,
              easing: Easing.inOut(Easing.cubic),
              toValue: 0,
              useNativeDriver: true
            })
          ]))
        ),
        Animated.delay(150)
      ])
    );
    animation.start();
    return () => {
      animation.stop();
      values.forEach((value) => value.setValue(0));
    };
  }, [values]);

  return (
    <View style={styles.dots}>
      {values.map((value, index) => (
        <Animated.View
          key={index}
          style={[
            styles.dot,
            {
              opacity: value.interpolate({ inputRange: [0, 1], outputRange: [0.35, 1] }),
              transform: [
                { translateY: value.interpolate({ inputRange: [0, 1], outputRange: [0, -5] }) },
                { scale: value.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1.12] }) }
              ]
            }
          ]}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  libraryScope: { paddingHorizontal: 20, paddingBottom: 8, color: theme.colors.textSecondary, fontSize: 12 },
  shell: {
    flex: 1,
    backgroundColor: theme.colors.background
  },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center"
  },
  appBody: {
    flex: 1
  },
  header: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: theme.spacing.lg,
    paddingBottom: theme.spacing.md,
    paddingTop: theme.spacing.sm
  },
  brandRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xs
  },
  brandMark: {
    width: 32,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 7,
    backgroundColor: theme.colors.accent,
    overflow: "hidden"
  },
  brandImage: {
    height: "100%",
    width: "100%"
  },
  appTitle: {
    ...theme.typography.heroTitle,
    fontSize: 23,
    letterSpacing: -0.65,
    lineHeight: 28
  },
  userChip: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  userAvatar: {
    alignItems: "center",
    backgroundColor: theme.colors.ink,
    borderRadius: 13,
    height: 26,
    justifyContent: "center",
    width: 26
  },
  userAvatarText: {
    color: theme.colors.inkText,
    fontFamily: theme.fonts.bold,
    fontSize: 11,
    fontWeight: "700"
  },
  userLabel: {
    ...theme.typography.meta,
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600"
  },
  content: {
    flex: 1
  },
  banner: {
    backgroundColor: theme.colors.warningBackground,
    borderColor: theme.colors.warningBorder,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    marginBottom: theme.spacing.sm,
    marginHorizontal: theme.spacing.lg,
    padding: theme.spacing.sm
  },
  bannerText: {
    ...theme.typography.meta,
    color: theme.colors.warningText,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600"
  },
  tabBar: {
    flexDirection: "row",
    marginTop: theme.spacing.xs,
    paddingHorizontal: theme.spacing.lg,
    paddingTop: 5,
    borderColor: theme.colors.border,
    borderTopWidth: 1,
    backgroundColor: theme.colors.card
  },
  tabButton: {
    flex: 1,
    minHeight: 50,
    alignItems: "center",
    justifyContent: "center",
    gap: 2
  },
  tabButtonActive: {
    borderTopColor: theme.colors.accent,
    borderTopWidth: 2,
    marginTop: -6,
    paddingTop: 4
  },
  tabButtonPressed: {
    opacity: 0.7
  },
  tabLabel: {
    ...theme.typography.meta,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600"
  },
  activeTabLabel: {
    color: theme.colors.accent
  },
  nameGate: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: theme.spacing.xl
  },
  namePanel: {
    gap: theme.spacing.sm
  },
  nameIcon: {
    width: 64,
    height: 64,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.colors.accent,
    marginBottom: theme.spacing.md,
    overflow: "hidden"
  },
  nameEyebrow: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.bold,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1.2
  },
  nameTitle: {
    ...theme.typography.screenTitle,
    fontSize: 32,
    lineHeight: 38
  },
  nameDescription: {
    ...theme.typography.body,
    color: theme.colors.textSecondary,
    marginBottom: theme.spacing.lg - theme.spacing.xs
  },
  nameFieldLabel: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.bold,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 1
  },
  nameInput: {
    minHeight: 54,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    borderColor: theme.colors.borderStrong,
    backgroundColor: theme.colors.card,
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.regular,
    fontSize: 15,
    paddingHorizontal: theme.spacing.md
  },
  primaryButton: {
    minHeight: 54,
    borderRadius: theme.radius.button,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.colors.accent
  },
  primaryButtonPressed: {
    backgroundColor: theme.colors.accentPressed
  },
  primaryButtonText: {
    color: theme.colors.card,
    fontFamily: theme.fonts.bold,
    fontSize: 15,
    fontWeight: "700"
  },
  disabledButton: {
    opacity: 0.45
  },
  screen: {
    flex: 1,
    paddingHorizontal: theme.spacing.lg
  },
  screenHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: theme.spacing.md
  },
  screenTitle: {
    ...theme.typography.screenTitle
  },
  iconButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderWidth: 1
  },
  iconButtonPressed: {
    backgroundColor: theme.colors.border
  },
  listContent: {
    gap: theme.spacing.xs,
    paddingBottom: theme.spacing.lg
  },
  emptyList: {
    flexGrow: 1,
    justifyContent: "center"
  },
  emptyState: {
    alignItems: "center",
    gap: theme.spacing.xs,
    paddingHorizontal: theme.spacing.lg
  },
  emptyIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderWidth: 1,
    ...theme.shadow
  },
  emptyHeadline: {
    ...theme.typography.screenTitle,
    fontSize: 22,
    lineHeight: 28,
    textAlign: "center"
  },
  emptyText: {
    ...theme.typography.body,
    color: theme.colors.textSecondary,
    maxWidth: 290,
    textAlign: "center"
  },
  pendingCard: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.sm,
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    marginBottom: theme.spacing.sm,
    borderLeftColor: theme.colors.accent,
    borderLeftWidth: 3,
    padding: theme.spacing.md
  },
  pendingTextWrap: {
    flex: 1,
    minWidth: 0
  },
  pendingTitle: {
    ...theme.typography.cardTitle,
    fontSize: 15,
    lineHeight: 20
  },
  pendingText: {
    ...theme.typography.meta,
    marginTop: theme.spacing.xxs
  },
  pendingUrl: {
    ...theme.typography.meta,
    color: theme.colors.accent,
    marginTop: theme.spacing.xxs
  },
  itemCard: {
    borderRadius: theme.radius.card,
    borderColor: theme.colors.border,
    borderWidth: 1,
    backgroundColor: theme.colors.card,
    padding: theme.spacing.md
  },
  itemCardPressed: {
    opacity: 0.75,
    transform: [{ scale: 0.99 }]
  },
  itemTile: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.tile
  },
  cardTopRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.sm,
    justifyContent: "space-between"
  },
  cardFooterRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: theme.spacing.sm
  },
  watchHint: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xxs
  },
  watchHintText: {
    ...theme.typography.meta,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600"
  },
  cardTitleWrap: {
    flex: 1,
    minWidth: 0
  },
  placeName: {
    ...theme.typography.cardTitle
  },
  itemMeta: {
    ...theme.typography.meta,
    marginTop: theme.spacing.xxs,
    textTransform: "capitalize"
  },
  saveCount: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xxs,
    backgroundColor: theme.colors.background,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    minHeight: 28,
    paddingHorizontal: theme.spacing.xs
  },
  saveCountText: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 13,
    fontWeight: "600"
  },
  chip: {
    alignSelf: "flex-start",
    borderRadius: theme.radius.chip,
    backgroundColor: theme.colors.background,
    flexShrink: 1,
    paddingHorizontal: theme.spacing.xs,
    paddingVertical: 5
  },
  chipText: {
    ...theme.typography.meta,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600",
    color: theme.colors.textPrimary
  },
  inlineError: {
    color: theme.colors.danger,
    fontFamily: theme.fonts.semibold,
    fontSize: 13,
    fontWeight: "600",
    marginBottom: theme.spacing.sm
  },
  askScreen: {
    flex: 1,
    paddingHorizontal: theme.spacing.lg
  },
  chatHeader: {
    alignItems: "center",
    borderBottomColor: theme.colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginHorizontal: -theme.spacing.lg,
    paddingBottom: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg
  },
  chatIdentity: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    minWidth: 0
  },
  botAvatar: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: 9,
    height: 38,
    justifyContent: "center",
    position: "relative",
    width: 38,
    overflow: "hidden"
  },
  onlineDot: {
    backgroundColor: "#65D69A",
    borderColor: theme.colors.background,
    borderRadius: 6,
    borderWidth: 2,
    bottom: -1,
    height: 12,
    position: "absolute",
    right: -1,
    width: 12
  },
  chatIdentityCopy: {
    flex: 1,
    minWidth: 0
  },
  chatTitleRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xs
  },
  chatTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.display,
    fontSize: 18,
    fontWeight: "700",
    lineHeight: 23
  },
  contextBadge: {
    alignItems: "center",
    backgroundColor: theme.colors.chatAqua,
    borderRadius: 4,
    flexDirection: "row",
    gap: 3,
    paddingHorizontal: 7,
    paddingVertical: 3
  },
  contextBadgeText: {
    color: theme.colors.chatAquaInk,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600"
  },
  chatLibraryButton: {
    alignItems: "center",
    alignSelf: "flex-start",
    flexDirection: "row",
    gap: 4,
    marginTop: 1,
    maxWidth: "100%",
    paddingVertical: 2
  },
  chatLibraryText: {
    color: theme.colors.textSecondary,
    flexShrink: 1,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600",
    lineHeight: 15
  },
  newChatButton: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    marginLeft: theme.spacing.sm,
    width: 44
  },
  answerCard: {
    flex: 1,
    borderRadius: theme.radius.card,
    borderColor: theme.colors.border,
    borderWidth: 1,
    backgroundColor: theme.colors.card,
    marginTop: theme.spacing.sm,
    marginBottom: theme.spacing.sm,
    padding: theme.spacing.md,
    ...theme.shadow
  },
  questionBlock: {
    borderBottomColor: theme.colors.border,
    borderBottomWidth: 1,
    marginBottom: theme.spacing.md,
    paddingBottom: theme.spacing.sm
  },
  questionLabel: {
    ...theme.typography.meta,
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600",
    marginBottom: theme.spacing.xxs
  },
  questionText: {
    ...theme.typography.body
  },
  answerText: {
    ...theme.typography.body,
    color: theme.colors.chatInk,
    fontSize: 15,
    lineHeight: 23
  },
  messageTextStack: {
    gap: 7
  },
  messageTextSpacer: {
    height: 3
  },
  messageSectionHeading: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.bold,
    fontSize: 14,
    fontWeight: "700",
    lineHeight: 20,
    marginTop: 3
  },
  messageBulletRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: theme.spacing.xs
  },
  messageBulletDot: {
    backgroundColor: theme.colors.accent,
    borderRadius: 3,
    height: 6,
    marginTop: 8,
    width: 6
  },
  messageBulletText: {
    color: theme.colors.chatInk,
    flex: 1,
    fontFamily: theme.fonts.regular,
    fontSize: 14,
    lineHeight: 21
  },
  messageBulletTitle: {
    fontFamily: theme.fonts.semibold,
    fontWeight: "600"
  },
  answerMuted: {
    ...theme.typography.body,
    color: theme.colors.textSecondary,
    textAlign: "center"
  },
  answerLoading: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.sm
  },
  chatArea: {
    flex: 1
  },
  chatScroll: {
    flexGrow: 1,
    paddingBottom: theme.spacing.lg,
    paddingTop: theme.spacing.lg
  },
  askIntro: {
    flex: 1,
    paddingBottom: theme.spacing.lg,
    paddingTop: theme.spacing.sm
  },
  chatHero: {
    borderBottomColor: theme.colors.borderStrong,
    borderBottomWidth: 1,
    minHeight: 220,
    paddingBottom: theme.spacing.lg,
    position: "relative"
  },
  heroOrbOne: {
    backgroundColor: "#3A4355",
    borderRadius: 88,
    height: 176,
    opacity: 0.72,
    position: "absolute",
    right: -68,
    top: -74,
    width: 176
  },
  heroOrbTwo: {
    backgroundColor: theme.colors.accent,
    borderRadius: 42,
    bottom: -48,
    height: 84,
    opacity: 0.26,
    position: "absolute",
    right: 42,
    width: 84
  },
  heroBotMark: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: 10,
    height: 44,
    justifyContent: "center",
    marginBottom: theme.spacing.md,
    overflow: "hidden",
    width: 44
  },
  chatHeroEyebrow: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.bold,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1.2,
    marginBottom: theme.spacing.xs
  },
  chatHeroTitle: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.display,
    fontSize: 30,
    fontWeight: "700",
    letterSpacing: -0.5,
    lineHeight: 36,
    maxWidth: "94%"
  },
  chatHeroText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 15,
    lineHeight: 22,
    marginTop: theme.spacing.xs,
    maxWidth: "92%"
  },
  heroCapabilities: {
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginTop: theme.spacing.md
  },
  heroCapability: {
    alignItems: "center",
    borderColor: theme.colors.border,
    borderRadius: 4,
    borderWidth: 1,
    flexDirection: "row",
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  heroCapabilityText: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600"
  },
  suggestionHeader: {
    alignItems: "flex-end",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: theme.spacing.sm,
    marginTop: theme.spacing.lg
  },
  suggestionHeading: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.display,
    fontSize: 19,
    fontWeight: "700"
  },
  suggestionHint: {
    ...theme.typography.meta,
    fontSize: 11
  },
  suggestionGrid: {
    gap: theme.spacing.xs
  },
  suggestionCard: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    minHeight: 66,
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: theme.spacing.xs,
    position: "relative"
  },
  suggestionCardPressed: {
    opacity: 0.74,
    transform: [{ scale: 0.98 }]
  },
  suggestionIcon: {
    alignItems: "center",
    borderRadius: 7,
    height: 32,
    justifyContent: "center",
    marginBottom: 5,
    width: 32
  },
  suggestionEyebrow: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600"
  },
  suggestionPrompt: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.semibold,
    fontSize: 14,
    fontWeight: "600",
    lineHeight: 18,
    marginRight: theme.spacing.md,
    marginTop: 3
  },
  suggestionArrow: {
    bottom: 25,
    position: "absolute",
    right: theme.spacing.sm
  },
  userMessageGroup: {
    alignItems: "flex-end",
    marginBottom: theme.spacing.md
  },
  messageMetaRight: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    marginBottom: 5,
    marginRight: 3
  },
  messageMetaLeft: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    marginBottom: 5,
    marginLeft: 2
  },
  messageAuthor: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600"
  },
  messageTime: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 10
  },
  questionBubble: {
    alignSelf: "flex-end",
    backgroundColor: theme.colors.ink,
    borderRadius: theme.radius.card,
    maxWidth: "88%",
    paddingHorizontal: theme.spacing.md,
    paddingVertical: 11
  },
  questionBubbleText: {
    ...theme.typography.body,
    color: theme.colors.inkText,
    fontSize: 15,
    lineHeight: 22
  },
  assistantMessageGroup: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginBottom: theme.spacing.md,
    width: "100%"
  },
  messageAvatarSmall: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: 6,
    height: 28,
    justifyContent: "center",
    marginTop: 18,
    overflow: "hidden",
    width: 28
  },
  assistantMessageBody: {
    flex: 1,
    minWidth: 0
  },
  answerBubble: {
    borderLeftColor: theme.colors.borderStrong,
    borderLeftWidth: 1,
    paddingBottom: theme.spacing.sm,
    paddingLeft: theme.spacing.md,
    paddingRight: theme.spacing.xs,
    paddingTop: theme.spacing.xxs
  },
  thinkingBubble: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    flexDirection: "row",
    height: 42,
    justifyContent: "center",
    minWidth: 64,
    paddingHorizontal: theme.spacing.md
  },
  playTile: {
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 9,
    backgroundColor: theme.colors.accentSoft
  },
  deleteButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 10,
    backgroundColor: theme.colors.dangerSoft
  },
  emptyImage: {
    width: 190,
    height: 190
  },
  folderGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: theme.spacing.sm
  },
  folderTileCard: {
    alignItems: "flex-start",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    flexBasis: "47%",
    flexGrow: 1,
    gap: theme.spacing.xxs,
    padding: theme.spacing.md,
    borderTopColor: theme.colors.accent,
    borderTopWidth: 3
  },
  folderTileIcon: {
    width: 42,
    height: 42,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.tile,
    marginBottom: theme.spacing.xs
  },
  folderTileName: {
    ...theme.typography.cardTitle,
    fontSize: 16
  },
  folderTileCount: {
    ...theme.typography.meta
  },
  folderRowIcon: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 12
  },
  groupSelector: {
    alignItems: "center",
    backgroundColor: "transparent",
    borderColor: theme.colors.border,
    borderBottomWidth: 1,
    borderTopWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    marginBottom: theme.spacing.sm,
    marginHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.sm
  },
  groupSelectorPressed: {
    opacity: 0.55
  },
  groupSelectorIcon: {
    alignItems: "center",
    borderRightColor: theme.colors.border,
    borderRightWidth: 1,
    height: 34,
    justifyContent: "center",
    width: 36
  },
  groupSelectorCopy: {
    flex: 1,
    minWidth: 0
  },
  groupSelectorEyebrow: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.bold,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.8
  },
  groupSelectorText: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.semibold,
    fontSize: 15,
    fontWeight: "600",
    lineHeight: 18,
    marginTop: 1
  },
  groupSelectorMeta: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    lineHeight: 14
  },
  groupSelectorAction: {
    alignItems: "center",
    flexDirection: "row",
    gap: 2,
    paddingHorizontal: 4,
    paddingVertical: 6
  },
  groupSelectorActionText: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600"
  },
  groupRowActive: {
    borderColor: theme.colors.accent
  },
  groupSheet: {
    backgroundColor: theme.colors.background,
    flex: 1
  },
  groupSheetHeader: {
    alignItems: "center",
    borderBottomColor: theme.colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingBottom: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg,
    paddingTop: theme.spacing.md
  },
  groupSheetEyebrow: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.bold,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 1
  },
  groupSheetTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.display,
    fontSize: 24,
    fontWeight: "700",
    lineHeight: 27,
    marginTop: 2
  },
  groupSheetClose: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  groupSheetBody: {
    paddingBottom: theme.spacing.xl,
    paddingHorizontal: theme.spacing.lg,
    paddingTop: theme.spacing.md
  },
  groupExplainer: {
    alignItems: "flex-start",
    backgroundColor: theme.colors.chatAqua,
    borderBottomColor: theme.colors.borderStrong,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    padding: theme.spacing.md
  },
  groupExplainerIcon: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.button,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  groupExplainerCopy: {
    flex: 1
  },
  groupExplainerTitle: {
    color: theme.colors.chatAquaInk,
    fontFamily: theme.fonts.bold,
    fontSize: 14,
    fontWeight: "700"
  },
  groupExplainerText: {
    color: theme.colors.chatAquaInk,
    fontFamily: theme.fonts.regular,
    fontSize: 12,
    lineHeight: 17,
    marginTop: 3,
    opacity: 0.88
  },
  groupSectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginBottom: theme.spacing.xs,
    marginTop: theme.spacing.lg
  },
  groupSectionTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.display,
    fontSize: 18,
    fontWeight: "700"
  },
  groupAddTitle: {
    marginBottom: theme.spacing.xs,
    marginTop: theme.spacing.lg
  },
  groupSectionCount: {
    backgroundColor: theme.colors.chatSurfaceStrong,
    borderRadius: theme.radius.chip,
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600",
    overflow: "hidden",
    paddingHorizontal: 7,
    paddingVertical: 3
  },
  groupEmptyState: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderStyle: "dashed",
    borderWidth: 1,
    padding: theme.spacing.lg
  },
  groupEmptyTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.semibold,
    fontSize: 13,
    fontWeight: "600",
    marginTop: theme.spacing.xs
  },
  groupEmptyText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    marginTop: 3,
    textAlign: "center"
  },
  groupCard: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    marginBottom: theme.spacing.sm,
    overflow: "hidden"
  },
  groupCardActive: {
    borderColor: theme.colors.accent,
    borderLeftWidth: 3
  },
  groupCardMain: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.sm,
    padding: theme.spacing.sm
  },
  groupCardIcon: {
    alignItems: "center",
    borderRadius: theme.radius.button,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  groupCardCopy: {
    flex: 1,
    minWidth: 0
  },
  groupCardTitleRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  groupCardTitle: {
    color: theme.colors.chatInk,
    flexShrink: 1,
    fontFamily: theme.fonts.semibold,
    fontSize: 14,
    fontWeight: "600"
  },
  activeGroupBadge: {
    backgroundColor: theme.colors.successSoft,
    borderRadius: 4,
    paddingHorizontal: 7,
    paddingVertical: 2
  },
  activeGroupBadgeText: {
    color: theme.colors.success,
    fontFamily: theme.fonts.bold,
    fontSize: 9,
    fontWeight: "700"
  },
  groupCardMeta: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    marginTop: 2
  },
  groupCodePill: {
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: theme.colors.cardMuted,
    borderRadius: theme.radius.chip,
    flexDirection: "row",
    gap: 5,
    marginTop: 7,
    paddingHorizontal: 8,
    paddingVertical: 4
  },
  groupCodeLabel: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.bold,
    fontSize: 7,
    fontWeight: "700",
    letterSpacing: 0.6
  },
  groupCodeValue: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.bold,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1
  },
  groupInviteButton: {
    alignItems: "center",
    borderTopColor: theme.colors.border,
    borderTopWidth: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    paddingVertical: 9
  },
  groupInviteButtonText: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600"
  },
  groupActionGrid: {
    flexDirection: "row",
    gap: theme.spacing.sm
  },
  groupActionCard: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    flex: 1,
    minHeight: 132,
    padding: theme.spacing.sm
  },
  groupActionCardActive: {
    borderColor: theme.colors.accent,
    borderWidth: 1.5
  },
  groupActionIcon: {
    alignItems: "center",
    backgroundColor: theme.colors.accentSoft,
    borderRadius: 6,
    height: 36,
    justifyContent: "center",
    marginBottom: theme.spacing.sm,
    width: 36
  },
  groupActionIconJoin: {
    backgroundColor: theme.colors.chatAqua
  },
  groupActionTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.semibold,
    fontSize: 13,
    fontWeight: "600"
  },
  groupActionText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 10,
    lineHeight: 14,
    marginTop: 3
  },
  groupActionPanel: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    marginTop: theme.spacing.sm,
    padding: theme.spacing.md
  },
  groupActionPanelTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.bold,
    fontSize: 15,
    fontWeight: "700"
  },
  groupActionPanelText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 3
  },
  groupActionInput: {
    backgroundColor: theme.colors.cardMuted,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.regular,
    fontSize: 14,
    marginTop: theme.spacing.sm,
    minHeight: 48,
    paddingHorizontal: theme.spacing.md
  },
  groupActionError: {
    color: theme.colors.danger,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    marginTop: theme.spacing.xs
  },
  groupActionSubmit: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: theme.radius.button,
    flexDirection: "row",
    gap: theme.spacing.xs,
    justifyContent: "center",
    marginTop: theme.spacing.sm,
    minHeight: 46
  },
  groupActionSubmitText: {
    color: theme.colors.card,
    fontFamily: theme.fonts.bold,
    fontSize: 13,
    fontWeight: "700"
  },
  modalHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.md
  },
  modalBody: {
    paddingBottom: theme.spacing.xl,
    paddingHorizontal: theme.spacing.lg
  },
  sectionLabel: {
    ...theme.typography.meta,
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600",
    marginBottom: theme.spacing.xs,
    marginTop: theme.spacing.md
  },
  formRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xs
  },
  formInput: {
    flex: 1,
    minHeight: 48,
    borderRadius: theme.radius.button,
    borderColor: theme.colors.border,
    borderWidth: 1,
    backgroundColor: theme.colors.card,
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.regular,
    fontSize: 15,
    paddingHorizontal: theme.spacing.md
  },
  formButton: {
    width: 48,
    height: 48,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.accent
  },
  folderScreen: {
    flex: 1,
    paddingHorizontal: theme.spacing.lg
  },
  folderSubtitle: {
    ...theme.typography.meta,
    color: theme.colors.textSecondary,
    marginTop: 2
  },
  folderSectionTabs: {
    backgroundColor: theme.colors.cardMuted,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    flexDirection: "row",
    gap: 4,
    marginTop: theme.spacing.md,
    padding: 4
  },
  folderSectionTab: {
    alignItems: "center",
    borderRadius: 10,
    flex: 1,
    flexDirection: "row",
    gap: 5,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 4
  },
  folderSectionTabActive: {
    backgroundColor: theme.colors.accent
  },
  folderSectionTabText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.bold,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.3
  },
  folderSectionTabTextActive: {
    color: theme.colors.card
  },
  folderPathRow: {
    alignItems: "center",
    borderBottomColor: theme.colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.xs,
    minHeight: 45
  },
  folderPathBack: {
    alignItems: "center",
    height: 44,
    justifyContent: "center",
    width: 44
  },
  folderPathText: {
    color: theme.colors.textPrimary,
    flex: 1,
    fontFamily: theme.fonts.semibold,
    fontSize: 14,
    fontWeight: "600"
  },
  folderList: {
    paddingBottom: theme.spacing.lg,
    paddingTop: theme.spacing.sm
  },
  breadcrumb: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xxs,
    paddingVertical: theme.spacing.sm
  },
  breadcrumbText: {
    ...theme.typography.screenTitle,
    flex: 1
  },
  folderRow: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    marginBottom: theme.spacing.sm,
    paddingHorizontal: theme.spacing.md,
    paddingVertical: theme.spacing.md
  },
  folderName: {
    ...theme.typography.body,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600",
    flex: 1
  },
  folderCount: {
    ...theme.typography.meta,
    color: theme.colors.textSecondary
  },
  folderNodeCopy: {
    flex: 1,
    minWidth: 0
  },
  folderNodeHint: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 10,
    marginTop: 2
  },
  folderItemRow: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    marginBottom: theme.spacing.sm,
    paddingHorizontal: theme.spacing.md,
    paddingVertical: theme.spacing.sm
  },
  folderItemBody: {
    flex: 1
  },
  folderItemSource: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.sm,
    minHeight: 44
  },
  connectionButton: {
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: theme.spacing.lg
  },
  folderItemTitle: {
    ...theme.typography.body,
    fontFamily: theme.fonts.semibold,
    fontWeight: "600"
  },
  folderItemMeta: {
    ...theme.typography.meta,
    color: theme.colors.textSecondary,
    marginTop: 2
  },
  sourcesBlock: {
    marginTop: theme.spacing.md,
    paddingTop: theme.spacing.xxs
  },
  sourcesHeadingRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 5,
    marginBottom: theme.spacing.xs
  },
  sourcesLabel: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 0.1
  },
  sourceCard: {
    alignItems: "center",
    backgroundColor: theme.colors.cardMuted,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginTop: 6,
    padding: theme.spacing.xs
  },
  sourceRowPressed: {
    opacity: 0.6
  },
  sourceIndex: {
    alignItems: "center",
    backgroundColor: theme.colors.accentSoft,
    borderRadius: 5,
    height: 32,
    justifyContent: "center",
    width: 32
  },
  sourceIndexText: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.bold,
    fontSize: 12,
    fontWeight: "700"
  },
  sourceCopy: {
    flex: 1,
    minWidth: 0
  },
  sourceTitle: {
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.semibold,
    fontSize: 12,
    fontWeight: "600"
  },
  sourceMeta: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 10,
    marginTop: 2
  },
  sourceOpenIcon: {
    alignItems: "center",
    backgroundColor: theme.colors.accentSoft,
    borderRadius: 5,
    height: 30,
    justifyContent: "center",
    width: 30
  },
  groupsScreen: {
    flex: 1,
    paddingHorizontal: theme.spacing.lg
  },
  groupsHeader: {
    alignItems: "flex-start",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: theme.spacing.md
  },
  groupsSubtitle: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 12,
    marginTop: 2
  },
  groupsHeaderActions: {
    flexDirection: "row",
    gap: theme.spacing.xs
  },
  groupsHeaderButton: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: 11,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  groupsInlineForm: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    marginBottom: theme.spacing.md,
    padding: theme.spacing.md
  },
  groupsInlineFormCopy: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  groupsInlineFormTitle: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.bold,
    fontSize: 14,
    fontWeight: "700"
  },
  groupsInlineInputRow: {
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginTop: theme.spacing.sm
  },
  groupsInlineInput: {
    backgroundColor: theme.colors.cardMuted,
    borderColor: theme.colors.border,
    borderRadius: 11,
    borderWidth: 1,
    color: theme.colors.textPrimary,
    flex: 1,
    fontFamily: theme.fonts.regular,
    fontSize: 14,
    minHeight: 44,
    paddingHorizontal: theme.spacing.md
  },
  groupsInlineSubmit: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: 11,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  groupsOverviewList: {
    gap: theme.spacing.sm,
    paddingBottom: theme.spacing.xl
  },
  groupsOverviewCard: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    padding: theme.spacing.md
  },
  groupsOverviewCardActive: {
    borderColor: theme.colors.accent,
    borderLeftWidth: 3
  },
  groupsOverviewIcon: {
    alignItems: "center",
    borderRadius: 14,
    height: 46,
    justifyContent: "center",
    width: 46
  },
  groupsOverviewCopy: {
    flex: 1,
    minWidth: 0
  },
  groupsOverviewTitleRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  groupsOverviewTitle: {
    color: theme.colors.textPrimary,
    flexShrink: 1,
    fontFamily: theme.fonts.bold,
    fontSize: 15,
    fontWeight: "700"
  },
  groupsActiveDot: {
    backgroundColor: theme.colors.success,
    borderRadius: 4,
    height: 7,
    width: 7
  },
  groupsOverviewMeta: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    marginTop: 3
  },
  groupsOverviewHint: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600",
    marginTop: 6
  },
  groupsBackText: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 13,
    fontWeight: "600"
  },
  groupDetailContent: {
    paddingBottom: theme.spacing.xl
  },
  groupDetailHero: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.md
  },
  groupDetailIcon: {
    alignItems: "center",
    backgroundColor: theme.colors.accentSoft,
    borderRadius: 17,
    height: 58,
    justifyContent: "center",
    width: 58
  },
  groupDetailCopy: {
    flex: 1,
    minWidth: 0
  },
  groupDetailEyebrow: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.bold,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.9
  },
  groupDetailTitle: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.display,
    fontSize: 25,
    fontWeight: "700",
    lineHeight: 29,
    marginTop: 1
  },
  groupDetailMeta: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    marginTop: 3
  },
  groupDetailDescription: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 12,
    lineHeight: 18,
    marginTop: theme.spacing.md
  },
  groupDetailActions: {
    flexDirection: "row",
    gap: theme.spacing.sm,
    marginTop: theme.spacing.md
  },
  groupPrimaryAction: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: 12,
    flex: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: 45
  },
  groupPrimaryActionText: {
    color: theme.colors.card,
    fontFamily: theme.fonts.bold,
    fontSize: 13,
    fontWeight: "700"
  },
  groupSecondaryAction: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: 45,
    paddingHorizontal: theme.spacing.md
  },
  groupSecondaryActionText: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 12,
    fontWeight: "600"
  },
  groupInviteCodeRow: {
    alignItems: "center",
    backgroundColor: theme.colors.cardMuted,
    borderRadius: 10,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: theme.spacing.sm,
    paddingHorizontal: theme.spacing.md,
    paddingVertical: theme.spacing.sm
  },
  groupInviteCodeLabel: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.bold,
    fontSize: 8,
    fontWeight: "700",
    letterSpacing: 0.8
  },
  groupInviteCodeValue: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.bold,
    fontSize: 13,
    fontWeight: "700",
    letterSpacing: 1.2
  },
  groupListHeadingRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginBottom: theme.spacing.sm,
    marginTop: theme.spacing.lg
  },
  groupListHeading: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.display,
    fontSize: 18,
    fontWeight: "700"
  },
  groupListCount: {
    backgroundColor: theme.colors.cardMuted,
    borderRadius: 8,
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    overflow: "hidden",
    paddingHorizontal: 7,
    paddingVertical: 3
  },
  groupLoadingArea: {
    alignItems: "center",
    minHeight: 130,
    justifyContent: "center"
  },
  groupItemsList: {
    gap: theme.spacing.xs
  },
  groupListEmpty: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderStyle: "dashed",
    borderWidth: 1,
    justifyContent: "center",
    margin: theme.spacing.lg,
    minHeight: 170,
    padding: theme.spacing.lg
  },
  groupListEmptyTitle: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.bold,
    fontSize: 14,
    fontWeight: "700",
    marginTop: theme.spacing.xs,
    textAlign: "center"
  },
  groupListEmptyText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 4,
    maxWidth: 260,
    textAlign: "center"
  },
  addReelsSheet: {
    backgroundColor: theme.colors.background,
    flex: 1
  },
  addReelsHeader: {
    alignItems: "center",
    borderBottomColor: theme.colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.md
  },
  addReelsHeaderCopy: {
    flex: 1,
    minWidth: 0
  },
  addReelsTitle: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.display,
    fontSize: 23,
    fontWeight: "700",
    marginTop: 2
  },
  addReelsSectionLabel: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.bold,
    fontSize: 8,
    fontWeight: "700",
    letterSpacing: 0.9,
    marginBottom: 7,
    marginHorizontal: theme.spacing.lg,
    marginTop: theme.spacing.md
  },
  addReelsChips: {
    gap: 7,
    paddingHorizontal: theme.spacing.lg
  },
  addReelsChipScroller: {
    flexGrow: 0,
    minHeight: 44,
    maxHeight: 44
  },
  filterChip: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.chip,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12
  },
  filterChipActive: {
    backgroundColor: theme.colors.accent,
    borderColor: theme.colors.accent
  },
  filterChipText: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600"
  },
  filterChipTextActive: {
    color: theme.colors.card
  },
  addReelsSelectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.md
  },
  addReelsSelectionCount: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.semibold,
    fontSize: 12,
    fontWeight: "600"
  },
  addReelsSelectAll: {
    color: theme.colors.accent,
    fontFamily: theme.fonts.semibold,
    fontSize: 11,
    fontWeight: "600"
  },
  addReelsList: {
    gap: 7,
    paddingBottom: 90,
    paddingHorizontal: theme.spacing.lg
  },
  addReelRow: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.sm,
    minHeight: 58,
    paddingHorizontal: theme.spacing.md,
    paddingVertical: theme.spacing.sm
  },
  addReelRowSelected: {
    backgroundColor: theme.colors.accentSoft,
    borderColor: theme.colors.accent
  },
  addReelCheck: {
    alignItems: "center",
    borderColor: theme.colors.borderStrong,
    borderRadius: 10,
    borderWidth: 1.5,
    height: 22,
    justifyContent: "center",
    width: 22
  },
  addReelCheckSelected: {
    backgroundColor: theme.colors.accent,
    borderColor: theme.colors.accent
  },
  addReelCopy: {
    flex: 1,
    minWidth: 0
  },
  addReelTitle: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.semibold,
    fontSize: 13,
    fontWeight: "600"
  },
  addReelMeta: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.regular,
    fontSize: 10,
    marginTop: 2
  },
  addReelsFooter: {
    backgroundColor: theme.colors.background,
    borderTopColor: theme.colors.border,
    borderTopWidth: 1,
    bottom: 0,
    left: 0,
    padding: theme.spacing.md,
    position: "absolute",
    right: 0
  },
  addReelsSubmit: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: 12,
    justifyContent: "center",
    minHeight: 48
  },
  addReelsSubmitText: {
    color: theme.colors.card,
    fontFamily: theme.fonts.bold,
    fontSize: 13,
    fontWeight: "700"
  },
  dots: {
    alignItems: "flex-end",
    flexDirection: "row",
    gap: 5,
    height: 14
  },
  dot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: theme.colors.accent
  },
  chatError: {
    alignItems: "center",
    backgroundColor: theme.colors.dangerSoft,
    borderColor: "#F1CFC7",
    borderRadius: 15,
    borderWidth: 1,
    flexDirection: "row",
    gap: theme.spacing.xs,
    marginBottom: theme.spacing.xs,
    padding: theme.spacing.xs
  },
  chatErrorIcon: {
    alignItems: "center",
    backgroundColor: theme.colors.card,
    borderRadius: 10,
    height: 32,
    justifyContent: "center",
    width: 32
  },
  chatErrorText: {
    color: theme.colors.danger,
    flex: 1,
    fontFamily: theme.fonts.regular,
    fontSize: 11,
    lineHeight: 15
  },
  retryButton: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.chip,
    paddingHorizontal: 10,
    paddingVertical: 7,
    minHeight: 44
  },
  retryButtonText: {
    color: theme.colors.danger,
    fontFamily: theme.fonts.bold,
    fontSize: 11,
    fontWeight: "700"
  },
  composerShell: {
    backgroundColor: theme.colors.card,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.card,
    borderWidth: 1,
    padding: 7
  },
  composerTopline: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    paddingBottom: 3,
    paddingHorizontal: theme.spacing.xs,
    paddingTop: 2
  },
  composerStatusDot: {
    backgroundColor: theme.colors.chatAquaInk,
    borderRadius: 3,
    height: 6,
    width: 6
  },
  composerHint: {
    color: theme.colors.textSecondary,
    fontFamily: theme.fonts.semibold,
    fontSize: 10,
    fontWeight: "600"
  },
  askComposer: {
    alignItems: "flex-end",
    flexDirection: "row",
    gap: 6
  },
  askInput: {
    flex: 1,
    minHeight: 42,
    maxHeight: 116,
    backgroundColor: "transparent",
    color: theme.colors.chatInk,
    fontFamily: theme.fonts.regular,
    fontSize: 15,
    lineHeight: 22,
    paddingHorizontal: theme.spacing.xs,
    paddingVertical: 9
  },
  sendButton: {
    alignItems: "center",
    backgroundColor: theme.colors.accent,
    borderRadius: theme.radius.button,
    height: 38,
    justifyContent: "center",
    marginBottom: 2,
    width: 38
  },
  sendButtonPressed: {
    backgroundColor: theme.colors.accentPressed,
    transform: [{ scale: 0.95 }]
  },
  sendButtonDisabled: {
    backgroundColor: theme.colors.chatSurfaceStrong
  }
});
