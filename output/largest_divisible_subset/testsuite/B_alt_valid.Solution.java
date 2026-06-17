import java.util.*;
class Solution {            // correct but returns the same set in reversed order
    public List<Integer> findLargestChain(int[] arr) {
        Arrays.sort(arr); int n = arr.length;
        int[] len = new int[n], prev = new int[n];
        Arrays.fill(len,1); Arrays.fill(prev,-1); int bi=0;
        for(int i=0;i<n;i++){ for(int j=0;j<i;j++) if(arr[i]%arr[j]==0 && len[j]+1>len[i]){len[i]=len[j]+1;prev[i]=j;} if(len[i]>=len[bi]) bi=i; }
        List<Integer> r=new ArrayList<>(); for(int i=bi;i>=0;i=prev[i]) r.add(arr[i]);
        Collections.reverse(r); return r;
    }
}
