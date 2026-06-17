import java.util.*;
class Solution {            // WRONG: returns the entire array (not pairwise divisible)
    public List<Integer> findLargestChain(int[] arr) {
        List<Integer> r=new ArrayList<>(); for(int x:arr) r.add(x); return r;
    }
}
