import com.xmilo_at_your_side.sign_to_speak.LocalAudioLease;

public final class LocalAudioLeaseTest {
  public static void main(String[] args) {
    LocalAudioLease owner = new LocalAudioLease();
    LocalAudioLease.Lease speech = owner.tryAcquire(new Object());
    if (speech == null || !owner.isHeld()) throw new AssertionError("first audio owner admitted");
    if (owner.tryAcquire(new Object()) != null) throw new AssertionError("microphone and TTS must exclude");
    speech.close(); speech.close();
    LocalAudioLease.Lease reply = owner.tryAcquire(new Object());
    if (reply == null) throw new AssertionError("confirmed cleanup permits next explicit owner");
    reply.close();
    System.out.println("LocalAudioLeaseTest passed");
  }
}
