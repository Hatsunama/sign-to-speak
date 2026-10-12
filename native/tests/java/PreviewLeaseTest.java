import com.xmilo_at_your_side.sign_to_speak.PreviewLease;

public final class PreviewLeaseTest {
  public static void main(String[] args) {
    PreviewLease lease = new PreviewLease();
    check(lease.begin(false, true) == -1, "denied camera permission");
    check(lease.begin(true, false) == -1, "background start");
    long first = lease.begin(true, true);
    check(lease.isCurrent(first), "valid active lease");
    lease.stop();
    check(!lease.isCurrent(first), "pending callback cancelled by stop");
    lease.stop();
    check(!lease.isCurrent(first), "repeated stop remains cancelled");
    long second = lease.begin(true, true);
    check(second != first && lease.isCurrent(second), "new foreground session");
    check(!lease.isCurrent(first), "old camera provider callback rejected");
    lease.begin(false, true);
    check(!lease.isCurrent(second), "permission revocation cancels active lease");
    System.out.println("PreviewLease: 8 checks passed");
  }
  private static void check(boolean value, String name) {
    if (!value) throw new AssertionError(name);
  }
}
