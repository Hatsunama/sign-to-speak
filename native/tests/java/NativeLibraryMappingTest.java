package com.xmilo_at_your_side.sign_to_speak;
import java.nio.file.*;
import java.util.zip.*;
import java.io.*;

public final class NativeLibraryMappingTest {
 public static void main(String[] args) throws Exception {
  Path file=Files.createTempFile("synthetic-native-map", ".apk");
  try {
   try(ZipOutputStream zip=new ZipOutputStream(Files.newOutputStream(file))) {
    byte[] data=new byte[65536]; CRC32 crc=new CRC32();crc.update(data);
    for(String library:new String[]{"libhermesvm.so","libreactnative.so","libappmodules.so"}) {
     ZipEntry e=new ZipEntry("lib/x86_64/"+library);e.setMethod(ZipEntry.STORED);e.setSize(data.length);e.setCompressedSize(data.length);e.setCrc(crc.getValue());zip.putNextEntry(e);zip.write(data);zip.closeEntry();
    }
   }
   String apk=file.toString(),dir="/test/private/lib/x86_64";
   for(String library:new String[]{"libhermesvm.so","libreactnative.so","libappmodules.so"}) {
    long[] range=NativeLibraryMapping.storedEntry(apk,"lib/x86_64/"+library);
    long offset=range[0]+4096;
    String line="100000-110000 r-xp "+Long.toHexString(offset)+" 00:01 1 "+apk+"\n";
    check(!line.contains(library),"Counterexample: APK-backed maps omit library filename");
    check(NativeLibraryMapping.executableMapping(library,line,apk,dir),"APK-backed executable mapping");
    check(!NativeLibraryMapping.executableMapping(library,line.replace("r-xp","r--p"),apk,dir),"Read-only mapping is not loaded executable proof");
    check(!NativeLibraryMapping.executableMapping(library,line.replace(apk,apk+".other"),apk,dir),"Wrong APK rejected");
    check(!NativeLibraryMapping.executableMapping(library,line.replace(Long.toHexString(offset),Long.toHexString(range[1])),apk,dir),"Out-of-entry offset rejected");
    String extracted="100000-110000 r-xp 00000000 00:01 1 "+dir+"/"+library;
    check(NativeLibraryMapping.executableMapping(library,extracted,apk,dir),"Extracted executable mapping");
    check(!NativeLibraryMapping.executableMapping(library,extracted.replace(dir,"/another/package"),apk,dir),"Another package library rejected");
   }
   System.out.println("NativeLibraryMapping: 21 synthetic fixture assertions passed; no emulator result.");
  } finally {Files.deleteIfExists(file);}
 }
 private static void check(boolean condition,String label){if(!condition)throw new AssertionError(label);}
}
